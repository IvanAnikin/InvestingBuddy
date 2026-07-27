# Session State — Phase 29C.2 CLOSED · Phase 29C.3 NEXT (policy + government connectors) (updated 2026-07-27)

> Resumable snapshot. Overwrite at each checkpoint (context-compaction skill).
> Keep decisions + evidence, not raw logs.

## Current position
- Branch: `main` @ **`80c8454`** (clean; 29C.2 merged). Autonomous multi-phase campaign (Phase 0 → 31).
- Phase / subphase: **Phase 29C.2 CLOSED + validated.** Current position = **Phase 29C.3 NEXT — policy + government reference connectors. Stage: NOT STARTED** (no branch, no PR).
- Umbrella: **Phase 29C 🟡 in progress** — 29C.1 CLOSED + 29C.2 CLOSED; **only 29C.3 (policy + government) remains — the LAST 29C subphase.**

## Phase 29C.2 — CLOSED (condensed evidence)
- **PR #62 `80c8454`** "add commodity and energy reference connectors (USGS/EIA/IEA/IRENA/ENTSO-E)" squash-merged to `main`; **Deploy API — Staging** success, API `/health` `commit_sha=80c8454` (3 stable polls); web unchanged (backend-only); **NO migration (head 011)**; **no new flag/host/endpoint**.
- Extended the 29C.1 reference-only macro layer to **commodity + energy** with **zero new wiring** — SAME generic `MacroReferenceConnector`, `collect_theme_macro_evidence` collector (now iterates `ALL_MACRO_SOURCES`), discovery-council `R#` path, report `industry_macro_context` block, existing `SOURCE_MACRO_ENABLED` flag. New `COMMODITY_ENERGY_SOURCES` table (+ combined `ALL_MACRO_SOURCES`), 5 official public agencies, all `commodity` provider type: **USGS** (`usgs.gov`, T3), **US EIA** (`eia.gov`, T2, no API key), **IEA** (`iea.org`, T3), **IRENA** (`irena.org`, T3), **ENTSO-E** (`transparency.entsoe.eu`, T3). Each emits ONE bounded **T2/T3 `macro_report` SOURCE REFERENCE** + honest `data_not_sourced` gap — **no figures/dates, network-free, no API key**. Registry promotes the 5 rows PLANNED→enabled → **21 enabled / 2 scaffolded / 9 planned** (only SEDAR+/ASX scaffolds; openbb + trade/procurement/patent stay planned; total 32).
- Tests **backend 2119 pass / 12 skip / 0 fail** (+27 net; adjacent count tests updated, no ripple), ruff clean, mypy `71` baseline no-new. Security PASS (pre-PR review APPROVED 10/10). Frontend N/A.
- **Staging VALIDATED-WITH-ENVIRONMENTAL-NOTE (2026-07-27):** no app-setting flip (`SOURCE_MACRO_ENABLED` already ON from 29C.1). **B:** registry/health show usgs/iea/irena/entsoe (commodity, T3) + eia (commodity, T2) enabled, 21/2/9, honest "reference only, no figures, no API key" notes, secret-free. **C:** uranium discovery run cites US EIA + IEA as `R#` macro refs + honest gaps, 0 figure tokens, candidates returned (CCJ/UEC/UUUU/FCX), macro is CONTEXT not a candidate, discovery council 8/8 — proves new sources surface. **D (PARTIAL, environmental):** Cameco + UEC reports render `industry_macro_context` reference-only (no figures), macro_context non-empty, schema/safety valid, publication_ready false, company_ir/SEC present — but the NEW specialist sources did NOT surface for these two (see carry-forward). **E:** no fabricated figures, forbidden terms only in negated disclaimers. **F:** company council 7/8 (1 agent Azure TPM ENVIRONMENTAL). **G:** logs clean, AUTH_TEST_MODE absent, admin-gated, 5 flags ON.
- Closure report: `docs/development/closures/phase-29c2.md`.

## ⚠️ CARRY-FORWARD classification note (KNOWN LIMITATION, NOT a defect)
- The **company-report** macro theme is derived from the company's **sector / industry**, which is **coarse for commodity / energy names** — `free_real` frequently returns `sector="Materials"` / no-industry, so the coarse company→theme derivation matches only the pre-existing 29C.1 World Bank 'Pink Sheet' keywords, not the new specialist commodity/energy keywords. So specialist sources (USGS / EIA / IEA) may **UNDER-SURFACE in company reports** until company→theme derivation is improved (e.g. map `sector + ticker/name` → the right specialist). **The sources are wired correctly and work at the discovery-council + registry level** (proven B + C; render unit-test-covered). **Future refinement** (Phase 29C follow-up / later) — record as a known limitation, do NOT treat as a bug.

## Decisions made (carried)
- **DEFERRAL: live commodity / energy FIGURE fetch DEFERRED (reference-only)** — no API keys (incl. EIA), no report-time network, evidence-first, honest `data_not_sourced` gaps. Keyless official-data-API fetch (USGS/EIA/IEA/IRENA/ENTSO-E) is a documented 29C follow-up.
- **REUSE the existing `SOURCE_MACRO_ENABLED` flag — NO new flag** (and no new host/endpoint/migration). The generic `MacroReferenceConnector` + `ALL_MACRO_SOURCES` single-source-of-truth pattern absorbs commodity/energy (and will absorb policy/government the same way).
- Macro / commodity / energy / policy is thesis-level / industry **CONTEXT only** — never company-specific, never a catalyst, never a recommendation; **no figures carried anywhere**; theme-scoped, not issuer-scoped.
- **Final staging flags KEPT ON (all 5, UNCHANGED):** `LLM_COUNCIL_ENABLED` · `LLM_DISCOVERY_COUNCIL_ENABLED` · `SOURCE_CONNECTOR_ENABLED` · `SOURCE_DOCUMENT_EXTRACTION_ENABLED` · `SOURCE_MACRO_ENABLED`.

## Phase 29C.1 — CLOSED (condensed, for context)
- **PR #61 `a8ac580`** merged + deployed + staging-validated (OFF-state clean; ON-state VALIDATED-WITH-ENVIRONMENTAL-NOTE after approved `SOURCE_MACRO_ENABLED` flip, kept ON). Reference-only macro layer (FRED/IMF/Eurostat/World Bank Pink Sheet/national stats+central banks). Closure: `docs/development/closures/phase-29c1.md`.

## Phase 29C.3 — NEXT (the LAST 29C subphase)
- **Scope:** policy + government reference connectors — defense / NATO spending, tariffs, subsidies, industrial policy, grid investment, energy transition. **Prefer official / government sources.**
- **Likely sources to promote** (currently PLANNED in the registry — check `/sources/registry` for the exact planned `trade_policy` / `procurement` / `government` rows): **USTR-TARIC**, **USAspending**, **EU TED**, **UN Comtrade** (national stats / central banks already enabled in 29C.1).
- **Same reference-only, OFF-by-default pattern** — reuse the generic `MacroReferenceConnector` + `ALL_MACRO_SOURCES` + existing `SOURCE_MACRO_ENABLED` flag (expect NO new flag/host/endpoint/migration); ONE bounded T2/T3 `macro_report` SOURCE REFERENCE + honest `data_not_sourced` gap per source; **no figures/dates, network-free, no API key, no fabricated data**; theme-scoped CONTEXT only; **no recommendations / valuations**; `human_review_required=true` / `publication_ready=false` unchanged; `/admin/*` OAuth-gated.
- **After 29C.3 the entire Phase 29C umbrella closes**; then Phase 29D (event-trigger/patents/local press), 30 (translation/local-language + PDF table extraction/OCR).

## Docs updated this checkpoint (ib-docs-agent, closure)
- `docs/development/closures/phase-29c2.md` (NEW closure report, carry-forward classification note prominent), `docs/development/PHASE_LEDGER.md` (29C.2 row → ✅ #62 `80c8454`; 29C umbrella note updated, stays 🟡), `docs/ROADMAP.md` (Current State: 29C.2 → ✅ COMPLETE deployed `80c8454`; 29C.3 set as next AND the LAST 29C subphase), this `session_state.md` (overwritten). `docs/API.md` / `docs/ARCHITECTURE.md` / `.env.example` / `DEPLOYMENT.md`: **n/a** (no API contract / architecture / flag / host / key change — they were already updated to 29C.2 during the PR checkpoint). Not committed — user reviews and commits.

## Next exact command / action
- **Scope Phase 29C.3 (policy + government connectors) and create branch `feature/phase-29c3-policy-government-connectors`.** Check the source registry for the planned `trade_policy` / `procurement` / `government` rows to promote (USTR-TARIC / USAspending / EU TED / UN Comtrade); apply the same reference-only, OFF-by-default pattern; prefer official / government sources.
