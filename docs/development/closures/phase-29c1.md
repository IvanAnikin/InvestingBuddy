# Closure Report — Phase 29C.1: Macro Baseline Evidence Connectors (reference-only, off by default)

> Produced ONLY after merge + deploy + staging validation. All SHAs and
> validation results below are real and verified this session (2026-07-27).
>
> **First subphase of the Phase 29C umbrella** (macro / commodity / policy
> evidence connectors). The Phase 29C umbrella stays 🟡 in progress — **29C.2
> commodity + energy** and **29C.3 policy + government** remain.

- **PR:** #61 "Phase 29C.1: add macro baseline evidence connectors (reference-only, off by default)" — squash-merged to `main`.
- **Merge SHA:** `a8ac580a1fd4b93eca19bc45252098d908091dd0`
- **API SHA** (`GET /health` `commit_sha`): `a8ac580` — matches merge SHA? yes (3 consecutive stable polls).
- **Web SHA** (`GET /api/version` `commit_sha`): unchanged — **expected**: backend-only PR, no web change this subphase.
- **Deploy:** "Deploy API — Staging" success at `a8ac580`. No web deploy (no web change).
- **Migration:** none — DB head `011` (unchanged).
- **AUTH_TEST_MODE:** absent — confirmed (protected routes challenge, not bypassed).
- **Tests:** backend **2092 pass / 12 skip / 0 fail** (+21 net in `apps/api/tests/test_phase29c1*`; adjacent registry/source-count tests updated for the five macro promotions, no ripple), ruff clean, mypy `71` pre-existing baseline (no new). Frontend N/A (backend-only).
- **Security / review:** ib-security-agent PASS (network-free at report time; no fabricated macro figure / index level / release date / forecast; fixed public token-free landing URLs; honest `data_not_sourced` gaps; OFF-by-default; no recommendation / rating / valuation output). Pre-PR review APPROVED (10/10).

## What 29C.1 shipped (backend-only, NO migration)
The platform's first **macro / thematic** evidence layer — **reference-only + OFF by default**:

- **`MacroReferenceConnector`** (`apps/api/app/services/sources/connectors/macro_reference.py`) — a
  generic connector over five official public macro sources — FRED (`fred.stlouisfed.org`), IMF,
  Eurostat, World Bank Commodity 'Pink Sheet', national statistics offices / central banks.
  Implements `fetch_macro_context` (previously a dead hook) to emit, for a relevant theme / region,
  ONE bounded **T2 `macro_report` SOURCE REFERENCE** (fixed public token-free landing URL + which
  indicators the dataset covers) **plus an honest `data_not_sourced` gap** ("live figures not
  fetched at report time"). **No indicator value / index level / release date / forecast is ever
  emitted; network-free; no API key** (FRED-style keys deliberately not introduced). `MACRO_SOURCES`
  is the single source of truth — registry and connectors are both built from it.
- **`collect_theme_macro_evidence(theme, region, cfg)`** (`sources/macro_evidence.py`) — the theme
  collector, **dark when `source_macro_enabled` is False**, bounded by `source_macro_max_items`
  (default 3).
- **Registry:** the five macro sources are promoted PLANNED → **enabled** → registry now
  **16 enabled / 2 scaffolded** (only SEDAR+ / ASX remain; total 32). `fred` / `imf` / `eurostat` /
  `national_stats_central_banks` = `macro_statistics`; `world_bank_pink_sheet` = `commodity`; all
  `T2_regulator_or_gov`.
- **Discovery council:** `build_discovery_evidence_pack` threads macro references as citeable `R#`
  run facts (in `evidence_ids()`) + honest gaps when `source_macro_enabled`.
- **Company report:** an OPTIONAL `industry_macro_context` block in `report_content` (beside
  `industry_context_events`), each item labelled macro CONTEXT — **NOT company-specific evidence,
  never a catalyst, no figures**. `CouncilResult.macro_context` surfaces via `to_metadata_dict`
  (empty `[]` when off, mirroring `primary_documents`). `schema_valid` / `safety_valid` stay true,
  `publication_ready` false, `human_review_required` true.
- **New flags** in `core/config.py`: `source_macro_enabled` (bool `False`) + `source_macro_max_items`
  (int `3`). **Dark-by-default: discovery pack + report body byte-identical when off.**

## Validation — OFF state (default posture)
- **VALIDATED (clean).** At `a8ac580` with `SOURCE_MACRO_ENABLED` at its default OFF: `/sources/registry`
  + `/sources/health` show **16 enabled / 2 scaffolded** with the five macro sources enabled,
  `T2_regulator_or_gov`, reference-only, honest reliability notes; the layer is **fully dark at
  runtime** (no macro item in any report body or discovery pack — byte-identical to Phase 29B); no
  regression; logs current-build clean; AUTH_TEST_MODE absent.

## Validation — ON state (human-approved `az` flip `SOURCE_MACRO_ENABLED=true`)
- **VALIDATED-WITH-ENVIRONMENTAL-NOTE.** After a human-approved staging flip of
  `SOURCE_MACRO_ENABLED=true`:
  - **Company report — SCCO (Southern Copper):** gains an `industry_macro_context` block
    (`world_bank_pink_sheet`, T2, token-free URL, **NO figures / dates**, honest
    "not company-specific / not a catalyst" note); `llm_council.macro_context` non-empty;
    `schema_valid=true`, `safety_valid=true`, `publication_ready=false`, `human_review_required=true`.
    Company council 7/8 agents (the 1 failure = Azure OpenAI gpt-4.1-mini TPM throttling =
    **environmental**, not a code defect).
  - **Discovery run — "copper mining":** cites macro references (World Bank ×3, Pink Sheet ×2) +
    honest gaps; a candidate is returned; **macro is CONTEXT, not a candidate**. Discovery council
    8/8 agents.
  - **Safety:** clean — no fabricated figures anywhere; forbidden reco/valuation terms appear only
    inside negated disclaimers.
  - **Logs:** current-build clean. **Publication:** admin-gated (no recommendation / valuation output).
- **Architecture note (verified):** macro evidence is **theme-scoped** (surfaces in reports +
  discovery), **NOT issuer-scoped** — correctly **absent** from the per-issuer company
  evidence-preview.

## Decision (recorded)
- **`SOURCE_MACRO_ENABLED` KEPT ON on staging.** Validation was clean, the layer is reference-only /
  low-risk, and this matches the standing decision to keep `SOURCE_CONNECTOR_ENABLED` and
  `SOURCE_DOCUMENT_EXTRACTION_ENABLED` on.

## Deliberate deferral (recorded — carried forward into Phase 29C)
- **Live macro-FIGURE fetch is DEFERRED (reference-only).** Mirrors the 29B.4 regulator
  content-fetch deferral — no API keys, no report-time network, evidence-first, honest gaps. A live
  **keyless official-data-API fetch** (World Bank / Eurostat / IMF etc.) is a documented **Phase 29C
  follow-up**. The shipped posture (T2 source reference + explicit `data_not_sourced` gap) satisfies
  all seven 29C.1 acceptance criteria.

## Limitations (honest — carry-forward candidates)
1. **No live macro figures** — the macro layer emits a T2 SOURCE REFERENCE + honest `data_not_sourced`
   gap only; no indicator value / index level / release date / forecast is fetched (deferred,
   reference-only; see above).
2. **Macro is theme / industry CONTEXT only** — never a company-specific claim, never a catalyst,
   never a recommendation; it appears beside `industry_context_events` and is honestly labelled.
3. Company council partial completion under large real-data packs remains an **Azure OpenAI
   gpt-4.1-mini TPM** environmental limit (7/8 on SCCO), not a code defect.
4. Non-English official macro docs would be flagged `requires_translation` (pending Phase 30) where
   applicable.

## Final flags (kept on staging — all ON)
`LLM_COUNCIL_ENABLED`=on · `LLM_DISCOVERY_COUNCIL_ENABLED`=on · `SOURCE_CONNECTOR_ENABLED`=on ·
`SOURCE_DOCUMENT_EXTRACTION_ENABLED`=on · **`SOURCE_MACRO_ENABLED`=on (NEW this phase, kept ON)**.

## Final verdict
**CLOSED + validated** — merged (`a8ac580`), deployed (API at `a8ac580`, 3 stable polls; web
unchanged by design), staging-validated both OFF (clean) and ON
(VALIDATED-WITH-ENVIRONMENTAL-NOTE). No DB migration (head `011`). Safety posture intact:
evidence-first, citation-bound, no recommendation / valuation output, admin-gated routes, human
approval before publication.

## Umbrella status — Phase 29C still 🟡 in progress
Phase 29C.1 is the **first** subphase of the Phase 29C umbrella (macro / commodity / policy evidence
connectors). With 29C.1 closed, the umbrella remains **in progress**:

- **29C.1 macro baseline** — PR #61 `a8ac580` (this report) ✅
- **29C.2 commodity + energy** — next (copper / lithium / rare-earths / uranium / nuclear /
  power-grid / electricity-demand; official sources USGS / EIA / IEA / IRENA / ENTSO-E / World Bank;
  same reference-only pattern) 🔜
- **29C.3 policy + government** — after 29C.2 🔜

**Next phase: Phase 29C.2** (commodity + energy evidence connectors, reference-only, OFF by default).
