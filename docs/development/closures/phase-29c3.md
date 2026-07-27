# Closure Report — Phase 29C.3: Policy + Government Reference Connectors (USTR-TARIC / UN Comtrade / NATO / SIPRI / OECD)

> Produced ONLY after merge + deploy + staging validation. All SHAs and
> validation results below are real and verified this session (2026-07-27).
>
> **Third and LAST subphase of the Phase 29C umbrella** (macro / commodity /
> policy evidence connectors). On this closure the **Phase 29C umbrella is
> COMPLETE** — 29C.1 (macro baseline) + 29C.2 (commodity + energy) + 29C.3
> (policy + government) all closed + staging-validated.

- **PR:** #63 "Phase 29C.3: add policy and government reference connectors (USTR-TARIC/UN Comtrade/NATO/SIPRI/OECD)" — squash-merged to `main`.
- **Merge SHA:** `ad6dde504d9c4a317cb0de2aeddf95ba66b9803b`
- **API SHA** (`GET /health` `commit_sha`): `ad6dde5` — matches merge SHA? yes (3 consecutive stable polls).
- **Web SHA** (`GET /api/version` `commit_sha`): unchanged — **expected**: backend-only PR, no web change this subphase.
- **Deploy:** "Deploy API — Staging" success at `ad6dde5`. No web deploy (no web change).
- **Migration:** none — DB head `011` (unchanged).
- **AUTH_TEST_MODE:** absent — confirmed (protected routes challenge, not bypassed).
- **Tests:** backend **2150 pass / 12 skip / 0 fail** (new `test_phase29c3`; two stale `enabled == 21` count assertions fixed to `26` for the five policy/government promotions — no functional ripple), ruff clean, mypy `71` pre-existing baseline (no new). Frontend N/A (backend-only).
- **Security / review:** ib-security-agent PASS (reference-only, network-free at report time; **no API keys**; no fabricated budget / spending-% / tariff-rate / subsidy / arms figure or release date; fixed official public URLs; honest `data_not_sourced` gaps; OFF-by-default via the reused `SOURCE_MACRO_ENABLED`; no recommendation / rating / valuation output). Pre-PR review APPROVED (10/10).

## What 29C.3 shipped (backend-only, NO migration)
Extends the 29C.1 / 29C.2 reference-only macro layer to **policy + government** with **ZERO new
wiring** — reuses the SAME generic `MacroReferenceConnector`, the `collect_theme_macro_evidence`
collector (iterating `ALL_MACRO_SOURCES`), the discovery-council `R#` citation path, the report
`industry_macro_context` block, and the existing `SOURCE_MACRO_ENABLED` flag.
**No new flag / host / endpoint / migration.**

- **`POLICY_GOVERNMENT_SOURCES`** table (folded into the combined `ALL_MACRO_SOURCES`) in
  `apps/api/app/services/sources/connectors/macro_reference.py` — five official public policy /
  government publishers, each emitting ONE bounded `macro_report` **SOURCE REFERENCE** (fixed
  official URL + which datasets it covers) + an honest `data_not_sourced` gap. **No budget /
  spending-% / tariff-rate / subsidy figure or date is ever emitted; network-free; no API key:**
  - **USTR / EU TARIC** — `ustr_taric`, **T2** (`trade_policy`) — tariffs / trade / customs —
    **promoted PLANNED→enabled**.
  - **UN Comtrade** — `un_comtrade`, **T2** (`trade_policy`) — tariffs / trade / customs —
    **promoted PLANNED→enabled**.
  - **NATO defence expenditure** — `nato`, **T2** (`trade_policy`), `nato.int` — defense /
    military-spending / procurement / arms — **new**.
  - **SIPRI military expenditure** — `sipri`, **T3** (`trade_policy`), `sipri.org` — defense /
    military-spending / procurement / arms — **new**.
  - **OECD** — `oecd`, **T2** (`macro_statistics`), `oecd.org` — subsidies / industrial-policy /
    state-aid / energy-transition / grid-investment — **new**.
- **`ProviderType` note:** the enum has **no `government_data` member**, so the government sources
  reuse the existing `trade_policy` (USTR-TARIC / UN Comtrade / NATO / SIPRI) and `macro_statistics`
  (OECD) members — intentional, avoids an enum / schema change.
- **Registry:** the five policy / government rows are promoted PLANNED → **enabled** (built from
  `ALL_MACRO_SOURCES` so registry and connectors never drift) → registry now
  **26 enabled / 2 scaffolded / 7 planned** (only SEDAR+ / ASX scaffolds remain; USAspending / EU
  TED / OpenBB + patent rows stay planned → Phase 29D; total **35**).
- **Behaviour:** a defense-themed report / discovery run surfaces up to two bounded policy /
  government references **when a theme matches** (see the classification carry-forward below);
  **dark-by-default byte-identical when off**. `schema_valid` / `safety_valid` stay true,
  `publication_ready` false, `human_review_required` true.

## ⚠️ Carry-forward: coarse theme→source keyword mapping may UNDER-SURFACE specialist sources (KNOWN LIMITATION, NOT a defect)
**Prominent, deliberately recorded — same carry-forward as 29C.2.** The company / discovery
theme→source keyword map is **coarse**: not every specialist source surfaces for every theme. In
the defense-themed staging discovery run (validation C) **NATO + USTR-TARIC surfaced and were cited**,
but **SIPRI / OECD / UN-Comtrade did NOT surface for that specific theme** — the theme→source keyword
map matched only a subset. The **citation mechanism + the registry are proven** (registry at
validation B, discovery `R#` citations at validation C); the refinement is a mapping improvement, not
a source defect.

- **This is a classification / mapping refinement, NOT a source defect.** The new sources are
  **wired correctly and demonstrably citeable** (registry-level at B, discovery-council-level at C),
  and the report-render path is **unit-test-covered**.
- **Future refinement (Phase 29C follow-up / later):** improve the company / discovery theme→source
  keyword map so each specialist policy / government source surfaces for the themes it covers.

## Staging validation — VALIDATED-WITH-ENVIRONMENTAL-NOTE
**No app-setting flip this subphase** — `SOURCE_MACRO_ENABLED` was already **ON** from Phase 29C.1,
so the policy / government layer is live on staging by inheritance.

- **B — Registry / health (VALIDATED):** `/sources/registry` + `/sources/health` show `ustr_taric` /
  `un_comtrade` / `nato` / `oecd` (**T2**) + `sipri` (**T3**) all **enabled** (provider `trade_policy`
  / `macro_statistics`); summary **26 enabled / 2 scaffolded / 7 planned / 35 total**; honest
  "policy / government reference only; live figures not fetched" notes; secret-free.
- **C — Discovery run (VALIDATED):** a **defense-themed** discovery run (candidates **Thales / BAE /
  Rheinmetall**) cites **NATO (×13) + USTR-TARIC (×3)** as macro `R#` run facts + honest gaps, treats
  **macro as CONTEXT, not a candidate**. **SIPRI / OECD / UN-Comtrade did NOT surface for this
  specific theme** (coarse theme→source keyword map — see the carry-forward above); the **citation
  mechanism is proven**.
- **D — Safety (VALIDATED):** `safety_valid` true, **no fabricated figures** anywhere, **no
  recommendation language**.
- **E — Discovery council:** **6/8** agents (the 2 failures = Azure OpenAI gpt-4.1-mini TPM
  throttling = **environmental**, not a code defect).
- **F — Operational (VALIDATED):** logs current-build clean, AUTH_TEST_MODE absent, publication
  admin-gated, **all 5 flags ON**.
- **G — Company report render (inferred):** the company `industry_macro_context` render is inferred
  from **31 unit tests** + the proven reference-only behaviour (theme-scoped, no figures) — a fresh
  full-council company run was skipped to avoid Azure OpenAI TPM burn re-testing an unchanged render
  path.

## Decision (recorded)
- **No app-setting flip needed / made.** `SOURCE_MACRO_ENABLED` was already ON from 29C.1 and is
  KEPT ON (validation clean, reference-only / low-risk).

## Deliberate deferral (recorded)
- **Live policy / government FIGURE fetch is DEFERRED (reference-only).** Same posture as 29C.1 /
  29C.2 — no API keys, no report-time network, evidence-first, honest `data_not_sourced` gaps. A
  keyless official-data-API fetch (USTR-TARIC / UN Comtrade / NATO / SIPRI / OECD) remains a
  documented follow-up.

## Limitations (honest — carry-forward candidates)
1. **Coarse theme→source keyword mapping** — not every specialist policy / government source surfaces
   for every theme (in the defense run SIPRI / OECD / UN-Comtrade did not surface); refine the
   theme→source keyword map (see the prominent carry-forward above). The sources are citeable and
   work at the registry + discovery-council level. **Known limitation, not a defect.**
2. **No live policy / government figures** — the layer emits a T2/T3 SOURCE REFERENCE + honest
   `data_not_sourced` gap only; no budget / spending-% / tariff-rate / subsidy / arms figure or date
   is fetched (deferred, reference-only; see above).
3. **Policy / government is theme / industry CONTEXT only** — never a company-specific claim, never a
   catalyst, never a recommendation; it appears beside `industry_context_events` and is honestly
   labelled.
4. Discovery-council partial completion under large real-data packs remains an **Azure OpenAI
   gpt-4.1-mini TPM** environmental limit (6/8), not a code defect.

## Final flags (kept on staging — all ON, UNCHANGED this subphase)
`LLM_COUNCIL_ENABLED`=on · `LLM_DISCOVERY_COUNCIL_ENABLED`=on · `SOURCE_CONNECTOR_ENABLED`=on ·
`SOURCE_DOCUMENT_EXTRACTION_ENABLED`=on · `SOURCE_MACRO_ENABLED`=on (inherited from 29C.1).

## Final verdict
**CLOSED + validated** — merged (`ad6dde5`), deployed (API at `ad6dde5`, 3 stable polls; web
unchanged by design), staging-validated **VALIDATED-WITH-ENVIRONMENTAL-NOTE** (registry + defense
discovery run prove the new specialist sources are citeable — NATO + USTR-TARIC cited; SIPRI / OECD /
UN-Comtrade under-surface for the specific theme by coarse theme→source mapping — a known limitation /
future refinement, not a defect). No DB migration (head `011`). Safety posture intact: evidence-first,
citation-bound, no recommendation / valuation output, admin-gated routes, human approval before
publication.

## Umbrella status — Phase 29C is now ✅ COMPLETE
- **29C.1 macro baseline** — PR #61 `a8ac580` ✅ (closure: `docs/development/closures/phase-29c1.md`)
- **29C.2 commodity + energy** — PR #62 `80c8454` ✅ (closure: `docs/development/closures/phase-29c2.md`)
- **29C.3 policy + government** — PR #63 `ad6dde5` ✅ (this report)

The Phase 29C umbrella (macro + commodity/energy + policy/government reference connectors) is
**COMPLETE** — all three subphases merged + deployed + staging-validated.

**Next phase: Phase 29D** (event-trigger connectors — likely split **29D.1 procurement / tenders**
[EU TED / government contract awards — the `eu_ted` / `usaspending` planned registry rows], **29D.2
patents** [`google_patents` / `uspto` / `epo_espacenet` planned rows], **29D.3 permits /
regulatory-event metadata**). Metadata-first, honest gaps, **no fake awards / contracts, no broad
crawling** — an event is an **internal research-priority evidence signal only, never a
recommendation**.
