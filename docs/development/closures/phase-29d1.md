# Closure Report — Phase 29D.1: Procurement / Tender Event-Trigger Reference Connectors (EU TED / USAspending)

> Produced ONLY after merge + deploy + staging validation. All SHAs and
> validation results below are real and verified this session (2026-07-28).
>
> **First subphase of the Phase 29D umbrella** (event-trigger connectors). The
> umbrella stays 🟡 IN PROGRESS — **29D.2 patents** and **29D.3 permits /
> regulatory-event metadata** remain.

- **PR:** #64 "Phase 29D.1: add procurement/tender event-trigger connectors (EU TED / USAspending)" — squash-merged to `main`.
- **Merge SHA:** `a671e9753d66554fb8dad1252686b1b7434e1a44`
- **API SHA** (`GET /health` `commit_sha`): `a671e97` — matches merge SHA? yes (3 consecutive stable polls).
- **Web SHA** (`GET /api/version` `commit_sha`): unchanged — **expected**: backend-only PR, no web change this subphase.
- **Deploy:** "Deploy API — Staging" success at `a671e97`. No web deploy (no web change).
- **Migration:** none — DB head `011` (unchanged).
- **AUTH_TEST_MODE:** absent — confirmed (protected routes challenge, not bypassed).
- **Tests:** backend **2184 pass / 12 skip / 0 fail** (+34; new `test_phase29d1`; two adjacent registry count assertions updated to **28 enabled / 5 planned** for the two procurement promotions — no functional ripple), ruff clean, mypy `71` pre-existing baseline (no new). Frontend N/A (backend-only).
- **Security / review:** ib-security-agent PASS (reference-only, network-free at report time; **no API keys**; `source_type="government_data"` — deliberately **NOT** `"government_contract"`; no fabricated tender / award / contractor / amount / contract-number / date; **WEAK** + `needs_human_review` labeling; event flag **independent** of the macro flag; no recommendation / rating / valuation output). Pre-PR review APPROVED (10/10).

## What 29D.1 shipped (backend-only, NO migration)
Establishes a NEW **event-trigger** evidence category — parallel to the 29C macro layer,
**reference-only + OFF by default** — under `apps/api/app/services/sources/`.

- **`EventReferenceConnector`** (`sources/connectors/event_reference.py`) over two official public
  procurement / tender venues — **EU TED** (`ted.europa.eu`) + **USAspending.gov**
  (`usaspending.gov`). The previously theme-dead `fetch_events` hook now emits, per relevant theme,
  ONE bounded **T2 SOURCE REFERENCE** (`source_type="government_data"` — deliberately NOT
  `"government_contract"`; `ProviderType` `procurement`; fixed official public URL, **no API key,
  NO specific tender / award / contractor / amount / contract-number / date**) + an honest
  `data_not_sourced` gap ("live tenders/awards not fetched at report time"). Each item is a **WEAK**
  internal research-priority signal that carries `needs_human_review`, records freshness via
  `stale_after_days`, and is **NOT a materiality claim / candidate / catalyst / trade signal** —
  network-free, never a recommendation.
- **`collect_theme_event_evidence(theme, region, cfg)`** (`sources/event_evidence.py`) — theme-keyed
  collector, **DARK** when `source_event_enabled` is False, bounded by `source_event_max_items`.
- **Registry:** `eu_ted` + `usaspending` promoted PLANNED → **enabled** event-reference sources
  (`procurement` / T2) → registry now **28 enabled / 2 scaffolded (SEDAR+ / ASX) / 5 planned
  (`google_patents` / `uspto` / `epo_espacenet` + `openbb` + local-language press → 29D.2 / later) /
  35 total**.
- **Discovery council:** when `source_event_enabled`, event references are threaded into
  `build_discovery_evidence_pack` as citeable `R#` run facts (in `evidence_ids()`) + honest gaps.
- **Company report:** OPTIONAL `industry_event_context` block in `report_content` (beside
  `industry_macro_context`), rendered only when `source_event_enabled` **and** a reference exists;
  labeled **WEAK event CONTEXT** — not company-specific, not a catalyst / materiality / trade signal,
  no figures. `CouncilResult.event_context` via `to_metadata_dict` (empty `[]` when off).
  `schema_valid` / `safety_valid` stay true, `publication_ready` false, `human_review_required` true.
- **New OFF-by-default flags** `SOURCE_EVENT_ENABLED` (false) + `SOURCE_EVENT_MAX_ITEMS` (3) in
  `core/config.py`, **INDEPENDENT of `SOURCE_MACRO_ENABLED`**. Dark-by-default: discovery pack +
  report body + macro layer byte-identical when off.

## Staging validation — OFF-state VALIDATED + ON-state VALIDATED-WITH-ENVIRONMENTAL-NOTE

- **OFF-state (default) — VALIDATED:** `/sources/registry` + `/sources/health` show **28 enabled /
  2 scaffolded / 5 planned / 35 total**; `eu_ted` + `usaspending` **enabled** `procurement` / **T2**;
  the layer is **fully dark at runtime** (byte-identical to Phase 29C), **macro independence
  confirmed** (event flag off leaves the macro layer unchanged), logs clean, AUTH_TEST_MODE absent.
- **ON-state (human-approved `az` flip `SOURCE_EVENT_ENABLED=true`) — VALIDATED-WITH-ENVIRONMENTAL-NOTE:**
  - **Discovery (VALIDATED):** a **"defense procurement Europe"** discovery run (candidates
    **Thales / BAE / Saab / Leonardo / Rheinmetall**) cites **USAspending (×3)** + **"EU TED and
    USAspending.gov"** as **reference-only** `R#` run facts + an honest gap ("live tenders / awards
    not fetched; only references to official datasets"). Events are **WEAK CONTEXT, NOT candidates**;
    safety valid — this **proves ON-state citeability**.
  - **Company report (VALIDATED, structurally):** a company report is valid (8/8 agents,
    `schema_valid` / `safety_valid` true, `publication_ready` false, `human_review_required` true),
    **but the `industry_event_context` + `industry_macro_context` blocks did NOT render for the
    tested European defense names** because their `free_real` profiles were **thin** (no sector /
    industry → empty theme → the collectors correctly stayed **dark**; the `event_context` /
    `macro_context` keys are **present-but-empty**). This is a **data-thinness environmental
    condition, NOT a code defect** — the render path is **unit-test-covered** and ON-state citeability
    is proven by the discovery run above.
  - **Councils:** **8/8** (no Azure OpenAI TPM failure this run).
  - **Operational:** logs current-build clean, publication admin-gated, **6 flags ON**.

## Decision (recorded)
- **`SOURCE_EVENT_ENABLED` KEPT ON on staging** (validation clean; reference-only / low-risk; matches
  keeping `SOURCE_MACRO_ENABLED` / `SOURCE_CONNECTOR_ENABLED` / `SOURCE_DOCUMENT_EXTRACTION_ENABLED`
  on).

## ⚠️ Carry-forward (KNOWN LIMITATION, NOT a defect — future refinement)
- **Company-report event / macro context blocks depend on a non-thin company sector / industry
  classification** (`free_real`) to derive a theme. Thin / mock profiles → the collectors stay dark →
  the `industry_event_context` / `industry_macro_context` blocks don't render. **Same
  coarse / thin company→theme carry-forward first noted in 29C.2.** The citation mechanism + the
  registry are proven (OFF-state registry + the ON-state discovery run); the render path is
  unit-test-covered. **Refine the company→theme derivation in a follow-up.**

## Deliberate deferrals (recorded)
- **Live tender / award FETCH is DEFERRED (reference-only)** — live EU TED / USAspending API fetch is
  a Phase 29D follow-up, mirroring the 29B.4 regulator content-fetch and 29C figure-fetch deferrals.
  No API keys, no report-time network, evidence-first, honest `data_not_sourced` gaps.
- **Full candidate-generation-from-events is DEFERRED** — AC1 is satisfied at the *context* level
  (discovery can cite event-trigger context); candidates still come from the curated registry.

## Limitations (honest — carry-forward candidates)
1. **Company→theme derivation from a thin / coarse company profile under-surfaces the event / macro
   context blocks** in company reports (present-but-empty when `free_real` returns no sector /
   industry). Known limitation, not a defect — refine the company→theme derivation (see the
   carry-forward above). Proven citeable at the registry + discovery-council level.
2. **No live tender / award figures** — the layer emits a T2 SOURCE REFERENCE + honest
   `data_not_sourced` gap only; no specific tender / award / contractor / amount / contract-number /
   date is fetched (deferred, reference-only; see above).
3. **Event references are theme / industry CONTEXT only** — never company-specific, never a catalyst /
   materiality / trade signal, never a candidate, never a recommendation; WEAK + `needs_human_review`.
4. **Azure OpenAI gpt-4.1-mini TPM quota** remains a standing staging environmental limiter (no
   council-agent failure this run), not a code defect.

## Final flags (kept on staging — all 6 ON)
`LLM_COUNCIL_ENABLED`=on · `LLM_DISCOVERY_COUNCIL_ENABLED`=on · `SOURCE_CONNECTOR_ENABLED`=on ·
`SOURCE_DOCUMENT_EXTRACTION_ENABLED`=on · `SOURCE_MACRO_ENABLED`=on · **`SOURCE_EVENT_ENABLED`=on
(NEW, kept ON)**.

## Final verdict
**CLOSED + validated** — merged (`a671e97`), deployed (API at `a671e97`, 3 stable polls; web
unchanged by design), staging-validated **OFF-state VALIDATED + ON-state
VALIDATED-WITH-ENVIRONMENTAL-NOTE** (OFF-state fully dark with macro independence confirmed; ON-state
citeability proven by the defense-procurement discovery run — USAspending / EU TED cited as
reference-only `R#` facts + honest gap; the company-report event / macro context blocks stayed dark
for thin `free_real` profiles — a data-thinness environmental condition + a known company→theme
refinement, not a defect). No DB migration (head `011`). Safety posture intact: evidence-first,
citation-bound, no recommendation / valuation output, admin-gated routes, human approval before
publication.

## Umbrella status — Phase 29D remains 🟡 IN PROGRESS
- **29D.1 procurement / tenders** — PR #64 `a671e97` ✅ (this report)
- **29D.2 patents** — `google_patents` / `uspto` / `epo_espacenet` PLANNED rows — **next**
  (reference-only, **no legal / infringement conclusions**)
- **29D.3 permits / regulatory-event metadata** — upcoming

Metadata-first, honest gaps, **no fake awards / contracts, no broad crawling** — an event is an
**internal research-priority evidence signal only, never a recommendation**.
