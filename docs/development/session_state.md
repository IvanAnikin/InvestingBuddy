# Session State — Phase 29C.1 Macro Reference Evidence Connectors (stage: PR about to open) (updated 2026-07-27)

> Resumable snapshot. Overwrite at each checkpoint (context-compaction skill).
> Keep decisions + evidence, not raw logs.

## Current position
- Branch: `feature/phase-29c1-macro-connectors`, HEAD **`bee8af3`** (clean). Autonomous multi-phase campaign (Phase 0 → 31).
- Phase / subphase: **Phase 29C.1 — IN PROGRESS (stage: PR about to open).** First Phase 29C subphase; the platform's first macro / thematic evidence layer. **NOT merged / deployed / staging-validated.**
- Umbrella: **Phase 29C 🟡 in progress** (29C.1 open; 29C.2 commodity+energy + 29C.3 policy+government still upcoming).
- Just finished (previous): **Phase 29B.4 umbrella COMPLETE** (4A `5138725` + 4B `1d97612` + 4C `de126ee`, all merged + deployed + staging-validated).

## What 29C.1 shipped (backend-only, 15 files incl. tests, NO migration — DB head `011`)
- Generic `MacroReferenceConnector` (`apps/api/app/services/sources/connectors/macro_reference.py`) over 5 official public sources: FRED (`fred.stlouisfed.org`), IMF, Eurostat, World Bank Commodity 'Pink Sheet', national statistics offices / central banks. `fetch_macro_context` (was a dead hook) emits ONE bounded **T2 `macro_report` SOURCE REFERENCE** (fixed public token-free landing URL + which indicators the dataset covers) + honest `data_not_sourced` gap. **Reference-only: no indicator value / index level / release date / forecast; network-free; NO API key.** `MACRO_SOURCES` is the single source of truth (registry + connectors built from it).
- `collect_theme_macro_evidence(theme, region, cfg)` (`sources/macro_evidence.py`): theme collector, DARK when `source_macro_enabled` False, bounded by `source_macro_max_items` (default 3).
- Registry: 5 macro sources promoted PLANNED → **enabled** → registry now **16 enabled / 2 scaffolded** (only SEDAR+/ASX remain; total 32). `fred`/`imf`/`eurostat`/`national_stats_central_banks` = `macro_statistics`; `world_bank_pink_sheet` = `commodity`; all `T2_regulator_or_gov`.
- Discovery council: `build_discovery_evidence_pack` threads macro refs as citeable `R#` run facts (in `evidence_ids()`) + honest gaps when `source_macro_enabled`.
- Company report: OPTIONAL `industry_macro_context` block in `report_content` (beside `industry_context_events`), each item labelled macro CONTEXT (NOT company-specific evidence, never a catalyst, no figures). `CouncilResult.macro_context` via `to_metadata_dict` (empty `[]` off, mirrors `primary_documents`). schema/safety valid, publication_ready false, human_review_required true.
- New flags in `core/config.py`: `source_macro_enabled` (bool `False`) + `source_macro_max_items` (int `3`). **Dark-by-default: discovery pack + report body byte-identical when off.**

## Verification (pre-staging)
- Tests: backend **2092 pass / 12 skip / 0 fail** (+`test_phase29c1_macro_connectors.py`), ruff clean, mypy **71** baseline (no new). Security scan **PASS** (network-free, no fabricated macro data, fixed public token-free URLs, honest gaps, OFF-by-default, no reco/valuation).
- No migration, no new host, no new endpoint.

## Decisions made (carried)
- **DELIBERATE: live macro-FIGURE fetch DEFERRED (reference-only).** Mirrors the 29B.4 regulator content-fetch deferral — no API keys, no report-time network, evidence-first, honest gaps. A live keyless official-data-API fetch (World Bank / Eurostat / IMF etc.) is a documented **29C follow-up**. This satisfies all seven 29C.1 acceptance criteria.
- New OFF-by-default flags `source_macro_enabled` (`False`) + `source_macro_max_items` (`3`); with the flag off the platform is byte-identical to Phase 29B.
- Macro is thesis-level / industry CONTEXT only — never a company-specific claim, never a catalyst, never a recommendation; no figures carried anywhere.
- Prior staging flags KEPT ON (from 29B): `LLM_COUNCIL_ENABLED`, `LLM_DISCOVERY_COUNCIL_ENABLED`, `SOURCE_CONNECTOR_ENABLED`, `SOURCE_DOCUMENT_EXTRACTION_ENABLED`. `SOURCE_MACRO_ENABLED` stays OFF until validated.

## Phase 29C remaining (upcoming)
- **29C.2 commodity + energy** (USGS / IEA / EIA / IRENA / ENTSO-E / World Bank Pink Sheet — incl. live commodity/energy figures).
- **29C.3 policy + government** (USTR-TARIC / USAspending / EU TED / UN Comtrade).

## Next exact command / action
- **run `ib-pr-review-agent`, then `gh pr create` for Phase 29C.1; STOP at the merge gate** (no merge/deploy without review + human approval).
