# Session State — Phase 29C.3 STAGE: PR (about to open) · policy + government reference connectors (updated 2026-07-27)

> Resumable snapshot. Overwrite at each checkpoint (context-compaction skill).
> Keep decisions + evidence, not raw logs.

## Current position
- Branch: `feature/phase-29c3-policy-government-connectors` @ **`2318a9b`** (clean). Autonomous multi-phase campaign (Phase 0 → 31).
- Phase / subphase: **Phase 29C.3 — policy + government reference connectors. Stage: PR (about to open).** Implementation committed + GREEN; **NOT merged/deployed/validated — do NOT mark ✅.**
- Umbrella: **Phase 29C 🟡 in progress** — 29C.1 CLOSED + 29C.2 CLOSED; **29C.3 (policy + government) is the LAST 29C subphase, now PR-open.** On 29C.3 merge + staging validation the whole 29C umbrella (macro + commodity/energy + policy/government) closes.
- Recent commits on branch: `2318a9b` (test: update stale registry enabled-count assertions 21→26) · `374d204` (Phase 29C.3: add policy + government reference connectors USTR-TARIC/UN Comtrade/NATO/SIPRI/OECD) · `21f808b` (docs: close 29C.2) · `80c8454` (29C.2 PR #62).

## Phase 29C.3 — what was built (condensed)
- Backend-only, ~9 files incl. tests, **NO migration** (DB head `011`), **no new host/endpoint, NO new flag** — reuses the existing OFF-by-default `SOURCE_MACRO_ENABLED`.
- New `POLICY_GOVERNMENT_SOURCES` table folded into the combined `ALL_MACRO_SOURCES`, served by the SAME generic `MacroReferenceConnector` (zero new wiring):
  - **USTR / EU TARIC** (`ustr_taric`, `trade_policy`, **T2**) — tariffs / trade / customs — **promoted PLANNED→enabled**.
  - **UN Comtrade** (`un_comtrade`, `trade_policy`, **T2**) — tariffs / trade / customs — **promoted PLANNED→enabled**.
  - **NATO defence expenditure** (`nato`, `trade_policy`, **T2**, `nato.int`) — defense / military-spending / procurement / arms — **new**.
  - **SIPRI military expenditure** (`sipri`, `trade_policy`, **T3**, `sipri.org`) — defense / military-spending / procurement / arms — **new**.
  - **OECD** (`oecd`, `macro_statistics`, **T2**, `oecd.org`) — subsidies / industrial-policy / state-aid / energy-transition / grid-investment — **new**.
- Each emits ONE bounded **T2/T3 `macro_report` SOURCE REFERENCE** (fixed official public URL + which datasets it covers) + honest `data_not_sourced` gap. **No budget / spending-% / tariff-rate / subsidy figure or date is ever emitted; network-free; no API key.**
- Reuses the SAME collector (`collect_theme_macro_evidence` iterates `ALL_MACRO_SOURCES`), discovery-council `R#` citation path, report `industry_macro_context` block. Registry: enabled **21→26**, scaffolded **2** (SEDAR+/ASX only), planned **9→7** (USAspending/EU TED/OpenBB + patent rows stay planned → 29D), total **32→35**.
- Tests **backend 2150 pass / 12 skip / 0 fail**, ruff clean, mypy `71` baseline no-new. **Security PASS.** Frontend N/A.

## Decisions made (carried)
- **REUSE the existing `SOURCE_MACRO_ENABLED` flag — NO new flag/host/endpoint/migration.** The generic `MacroReferenceConnector` + `ALL_MACRO_SOURCES` single-source-of-truth pattern absorbs policy/government exactly like it absorbed commodity/energy in 29C.2.
- **`ProviderType` has NO `government_data` member** — so government sources use the existing `trade_policy` (USTR-TARIC / UN Comtrade / NATO / SIPRI) and `macro_statistics` (OECD) members. Intentional, avoids an enum/schema change.
- **DEFERRAL: live policy / government FIGURE fetch DEFERRED (reference-only)** — no API keys, no report-time network, evidence-first, honest `data_not_sourced` gaps. Keyless official-data-API fetch (USTR-TARIC / UN Comtrade / NATO / SIPRI / OECD) is a documented follow-up.
- Macro / commodity / energy / policy / government is thesis-level / industry **CONTEXT only** — never company-specific, never a catalyst, never a recommendation; **no figures carried anywhere**; theme-scoped, not issuer-scoped.
- **Dark-by-default** — with `SOURCE_MACRO_ENABLED` off the discovery pack + report body are byte-identical to Phase 29C.2.
- Staging flags currently ON (unchanged from 29C.2, all 5): `LLM_COUNCIL_ENABLED` · `LLM_DISCOVERY_COUNCIL_ENABLED` · `SOURCE_CONNECTOR_ENABLED` · `SOURCE_DOCUMENT_EXTRACTION_ENABLED` · `SOURCE_MACRO_ENABLED`.

## Carry-forward (from 29C.2, still open — NOT a 29C.3 defect)
- Company-report macro theme is derived from the company's **sector / industry**, which is **coarse for commodity / energy names** (`free_real` often returns `sector="Materials"`/no-industry), so specialist sources may **UNDER-SURFACE in company reports** until company→theme derivation is improved. Sources work correctly at discovery-council + registry level. Future refinement, not a bug.

## Docs updated this checkpoint (ib-docs-agent — PR-open, NOT closed)
- `docs/ARCHITECTURE.md` (Status → Phase 29C.3 🟡 PR-open; demoted 29C.2 lead-in to merged+validated `80c8454`; registry 26 enabled / 2 scaffolded / 7 planned / 35 total; added 5 policy/gov sources + `ProviderType` note to the source-framework layer; Phase History: 29C.2 row → ✅ Complete + new 🟡 29C.3 row).
- `docs/API.md` (Status → 29C.3; `/sources/registry` summary `enabled:26 / scaffolded:2 / planned:7 / total:35`; registry + health + macro Sources section list `ustr_taric`/`un_comtrade`/`nato`/`sipri`/`oecd` enabled with tiers + `ProviderType` note; deferral extended to policy/government; no new endpoint).
- `docs/ROADMAP.md` (new Current State: 29C.3 🟡 PR-open / pre-staging — NOT complete; 29C.2 demoted to Previously ✅; noted 29C.3 is the LAST 29C subphase, Phase 29D next once umbrella validates; live-figure deferral noted).
- `docs/development/PHASE_LEDGER.md` (new 29C.3 row 🟡 in progress; 29C umbrella note updated, stays 🟡 until 29C.3 validates; NOT ✅).
- `.env.example` / `docs/DEPLOYMENT.md`: **n/a** — no new flag/host/key/migration (reuses `SOURCE_MACRO_ENABLED`, already present).
- NOT committed — user reviews and commits.

## Next exact command / action
- **Run `ib-pr-review-agent` on the 29C.3 diff, then `gh pr create` for `feature/phase-29c3-policy-government-connectors` (base `main`).** STOP at the merge gate — do NOT merge/deploy/mark closed until human approval + staging validation is on file. After validation: close 29C.3, then close the whole Phase 29C umbrella, then scope Phase 29D (event-trigger: procurement/tenders/patents/permits).
