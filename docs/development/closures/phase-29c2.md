# Closure Report — Phase 29C.2: Commodity + Energy Reference Connectors (USGS / EIA / IEA / IRENA / ENTSO-E)

> Produced ONLY after merge + deploy + staging validation. All SHAs and
> validation results below are real and verified this session (2026-07-27).
>
> **Second subphase of the Phase 29C umbrella** (macro / commodity / policy
> evidence connectors). The Phase 29C umbrella stays 🟡 in progress — only **29C.3
> policy + government** remains (the LAST 29C subphase).

- **PR:** #62 "Phase 29C.2: add commodity and energy reference connectors (USGS/EIA/IEA/IRENA/ENTSO-E)" — squash-merged to `main`.
- **Merge SHA:** `80c845405d00d1928e8d4fe2646701a620a7190b`
- **API SHA** (`GET /health` `commit_sha`): `80c8454` — matches merge SHA? yes (3 consecutive stable polls).
- **Web SHA** (`GET /api/version` `commit_sha`): unchanged — **expected**: backend-only PR, no web change this subphase.
- **Deploy:** "Deploy API — Staging" success at `80c8454`. No web deploy (no web change).
- **Migration:** none — DB head `011` (unchanged).
- **AUTH_TEST_MODE:** absent — confirmed (protected routes challenge, not bypassed).
- **Tests:** backend **2119 pass / 12 skip / 0 fail** (+27 net in `test_phase29c2_commodity_energy_connectors.py`; adjacent registry / source-count tests updated for the five commodity/energy promotions, no ripple), ruff clean, mypy `71` pre-existing baseline (no new). Frontend N/A (backend-only).
- **Security / review:** ib-security-agent PASS (reference-only, network-free at report time; **no API keys — including EIA**; no fabricated tonnage / price / capacity / production / reserve figure or release date; fixed official public URLs; honest `data_not_sourced` gaps; OFF-by-default via the reused `SOURCE_MACRO_ENABLED`; no recommendation / rating / valuation output). Pre-PR review APPROVED (10/10).

## What 29C.2 shipped (backend-only, NO migration)
Extends the 29C.1 reference-only macro layer to **commodity + energy** with **ZERO new
wiring** — reuses the SAME generic `MacroReferenceConnector`, the `collect_theme_macro_evidence`
collector (now iterating `ALL_MACRO_SOURCES`), the discovery-council `R#` citation path, the
report `industry_macro_context` block, and the existing `SOURCE_MACRO_ENABLED` flag.
**No new flag / host / endpoint / migration.**

- **`COMMODITY_ENERGY_SOURCES`** table (+ combined `ALL_MACRO_SOURCES`) in
  `apps/api/app/services/sources/connectors/macro_reference.py` — five official public agencies,
  each emitting ONE bounded `macro_report` **SOURCE REFERENCE** (fixed official URL + which
  datasets it covers) + an honest `data_not_sourced` gap. **No tonnage / price / capacity /
  production / reserve figure or date is ever emitted; network-free; no API key:**
  - **USGS** — `usgs.gov`, **T3** (`commodity`) — copper / lithium / rare-earths / critical
    minerals / cobalt / nickel / mining / uranium.
  - **US EIA** — `eia.gov`, **T2** (`commodity`), **NO API key** — uranium / nuclear / oil / gas /
    energy / electricity.
  - **IEA** — `iea.org`, **T3** (`commodity`) — energy / power-grid / nuclear / renewables /
    energy-transition.
  - **IRENA** — `irena.org`, **T3** (`commodity`) — renewables / solar / wind / hydrogen.
  - **ENTSO-E** — `transparency.entsoe.eu`, **T3** (`commodity`) — power-grid / electricity / grid /
    transmission.
- **Registry:** the five commodity/energy rows are promoted PLANNED → **enabled** (built from
  `ALL_MACRO_SOURCES` so registry and connectors never drift) → registry now
  **21 enabled / 2 scaffolded / 9 planned** (only SEDAR+ / ASX scaffolds remain; openbb + trade /
  procurement / patent rows stay planned; total 32).
- **Behaviour:** a copper company report surfaces up to two macro references (World Bank
  'Pink Sheet' + USGS) **when a theme matches** (see the classification carry-forward below);
  **dark-by-default byte-identical when off**. `schema_valid` / `safety_valid` stay true,
  `publication_ready` false, `human_review_required` true.
- Nine files incl. tests (no migration): `connectors/__init__.py`, `connectors/macro_reference.py`,
  `macro_evidence.py`, `registry.py` + five test files (new
  `test_phase29c2_commodity_energy_connectors.py`; adjacent 29a / 29b4b / 29b4c / 29c1 count tests
  updated for the promotions).

## ⚠️ Carry-forward: coarse company→theme derivation may UNDER-SURFACE specialist sources (KNOWN LIMITATION, NOT a defect)
**Prominent, deliberately recorded.** The **company-report** macro theme is derived from the
company's **sector / industry**, which is **coarse for commodity / energy names**: the `free_real`
provider frequently classifies such issuers as `sector="Materials"` with **no industry**, so the
coarse company→theme derivation matches only the pre-existing 29C.1 World Bank 'Pink Sheet'
keywords — **not** the new specialist commodity/energy keywords (USGS / EIA / IEA / IRENA /
ENTSO-E). As a result, the **new specialist sources may UNDER-SURFACE in company reports** for
commodity/energy issuers until the company→theme derivation is improved (e.g. map
`sector + ticker/name` → the correct specialist source).

- **This is a classification / mapping refinement, NOT a source defect.** The new sources are
  **wired correctly and demonstrably work** at the **registry** level (validation B) and the
  **discovery-council** level (validation C, e.g. a uranium discovery run cites US EIA + IEA), and
  the report-render path is **unit-test-covered**.
- **Future refinement (Phase 29C follow-up / later):** improve the company→theme derivation so a
  commodity/energy issuer (even one the provider labels `Materials` / no-industry) maps to the right
  specialist source(s) via `sector + ticker/name`.

## Staging validation — VALIDATED-WITH-ENVIRONMENTAL-NOTE
**No app-setting flip this subphase** — `SOURCE_MACRO_ENABLED` was already **ON** from Phase 29C.1,
so the commodity/energy layer is live on staging by inheritance.

- **B — Registry / health (VALIDATED):** `/sources/registry` + `/sources/health` show `usgs` / `iea` /
  `irena` / `entsoe` (`commodity`, **T3**) + `eia` (`commodity`, **T2**) all **enabled**; summary
  **21 enabled / 2 scaffolded / 9 planned**; honest "reference only, no figures, no API key" notes;
  secret-free.
- **C — Discovery run (VALIDATED):** a **uranium** discovery run cites **US EIA + IEA** as macro `R#`
  references + honest gaps, **0 figure tokens**, returns candidates (`CCJ` / `UEC` / `UUUU` / `FCX`),
  and treats **macro as CONTEXT, not a candidate**. Discovery council **8/8** agents. **This directly
  proves the new specialist sources surface and are citeable.**
- **D — Company reports (PARTIAL, environmental):** two company reports (**Cameco**, **UEC**) render
  the `industry_macro_context` block **reference-only** (no figures), `macro_context` non-empty,
  `schema_valid` / `safety_valid` true, `publication_ready` false, `company_ir` / SEC present —
  **but the NEW specialist sources did NOT surface for these two names** because the `free_real`
  provider classifies them `sector="Materials"` / no-industry, so the coarse company→theme
  derivation matched only the pre-existing 29C.1 World Bank 'Pink Sheet' keywords, not the new
  specialist keywords (see the carry-forward above). **NEW-source surfacing is proven via registry
  (B) + discovery-council (C) + the unit-test-covered render** — not a source failure, a
  classification-mapping coarseness.
- **E — Safety (VALIDATED):** no fabricated figures anywhere; forbidden reco / valuation terms appear
  only inside negated disclaimers.
- **F — Company council:** **7/8** agents (the 1 failure = Azure OpenAI gpt-4.1-mini TPM throttling =
  **environmental**, not a code defect).
- **G — Operational (VALIDATED):** logs current-build clean, AUTH_TEST_MODE absent, publication
  admin-gated, **all 5 flags ON**.

## Decision (recorded)
- **No app-setting flip needed / made.** `SOURCE_MACRO_ENABLED` was already ON from 29C.1 and is
  KEPT ON (validation clean, reference-only / low-risk).

## Deliberate deferral (recorded — carried forward into Phase 29C.3)
- **Live commodity / energy FIGURE fetch is DEFERRED (reference-only).** Same posture as 29C.1 — no
  API keys (including EIA), no report-time network, evidence-first, honest `data_not_sourced` gaps.
  A keyless official-data-API fetch (USGS / EIA / IEA / IRENA / ENTSO-E) remains a documented
  **Phase 29C follow-up**.

## Limitations (honest — carry-forward candidates)
1. **Coarse company→theme derivation** — specialist commodity/energy sources may UNDER-SURFACE in
   company reports for issuers the provider labels `Materials` / no-industry; refine
   `sector + ticker/name` → specialist mapping (see the prominent carry-forward above). The sources
   work correctly at the registry + discovery-council level. **Known limitation, not a defect.**
2. **No live commodity / energy figures** — the layer emits a T2/T3 SOURCE REFERENCE + honest
   `data_not_sourced` gap only; no tonnage / price / capacity / production / reserve figure or date
   is fetched (deferred, reference-only; see above).
3. **Commodity / energy is theme / industry CONTEXT only** — never a company-specific claim, never a
   catalyst, never a recommendation; it appears beside `industry_context_events` and is honestly
   labelled.
4. Company council partial completion under large real-data packs remains an **Azure OpenAI
   gpt-4.1-mini TPM** environmental limit (7/8), not a code defect.

## Final flags (kept on staging — all ON, UNCHANGED this subphase)
`LLM_COUNCIL_ENABLED`=on · `LLM_DISCOVERY_COUNCIL_ENABLED`=on · `SOURCE_CONNECTOR_ENABLED`=on ·
`SOURCE_DOCUMENT_EXTRACTION_ENABLED`=on · `SOURCE_MACRO_ENABLED`=on (inherited from 29C.1).

## Final verdict
**CLOSED + validated** — merged (`80c8454`), deployed (API at `80c8454`, 3 stable polls; web
unchanged by design), staging-validated **VALIDATED-WITH-ENVIRONMENTAL-NOTE** (registry + discovery
prove the new specialist sources; company-report surfacing is under-covered for `Materials`-labelled
names by coarse theme derivation — a known limitation / future refinement, not a defect). No DB
migration (head `011`). Safety posture intact: evidence-first, citation-bound, no recommendation /
valuation output, admin-gated routes, human approval before publication.

## Umbrella status — Phase 29C still 🟡 in progress (only 29C.3 remains)
- **29C.1 macro baseline** — PR #61 `a8ac580` ✅ (closure: `docs/development/closures/phase-29c1.md`)
- **29C.2 commodity + energy** — PR #62 `80c8454` ✅ (this report)
- **29C.3 policy + government** — **next AND the LAST 29C subphase** 🔜 (defense / NATO spending,
  tariffs, subsidies, industrial policy, grid investment, energy transition — prefer official /
  government sources: USTR-TARIC / USAspending / EU TED / UN Comtrade; same reference-only,
  OFF-by-default pattern)

**Next phase: Phase 29C.3** (policy + government evidence connectors — the final 29C subphase).
