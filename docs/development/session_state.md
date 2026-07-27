# Session State — Phase 29D.1 Procurement / Tender Event-Trigger Reference Connectors · Stage: PR (about to open) (updated 2026-07-28)

> Resumable snapshot. Overwrite at each checkpoint (context-compaction skill).
> Keep decisions + evidence, not raw logs.

## Current position
- Branch: `feature/phase-29d1-procurement-tender-connectors` @ **`63eafa3`** (clean). Autonomous multi-phase campaign (Phase 0 → 31).
- Phase / subphase: **Phase 29D.1 — procurement / tender event-trigger reference connectors.** **Stage: PR — about to open. NOT merged / deployed / staging-validated.**
- Umbrella: **Phase 29D — event-trigger connectors: 🟡 IN PROGRESS** (29D.1 is the first subphase). Prior umbrella **Phase 29C ✅ COMPLETE** (29C.1 `a8ac580` + 29C.2 `80c8454` + 29C.3 `ad6dde5`, all merged + staging-validated).
- Verified GREEN (pre-staging): backend **2184 pass / 12 skip / 0 fail**, ruff clean, mypy `71` baseline no-new, **security PASS**. 18 files (incl. tests), backend-only, **NO migration** (DB head stays `011`).

## Phase 29D.1 — what shipped (pre-PR, condensed)
- First Phase 29D subphase: a NEW **event-trigger** evidence category (parallel to the 29C macro layer), **reference-only, OFF by default**.
- New `EventReferenceConnector` (`sources/connectors/event_reference.py`) over 2 procurement/tender venues — **EU TED** (`ted.europa.eu`) + **USAspending.gov** (`usaspending.gov`). The previously theme-dead `fetch_events` hook now emits, per relevant theme, ONE bounded **T2 SOURCE REFERENCE** (`source_type="government_data"` — deliberately NOT "government_contract"; `ProviderType` `procurement`; fixed official public URL, no API key, **NO specific tender/award/contractor/amount/contract-number/date**) + honest `data_not_sourced` gap ("live tenders/awards not fetched at report time"). Each item is a **WEAK** internal research-priority signal + `needs_human_review`, records freshness via `stale_after_days`, and is **NOT a materiality claim / candidate / catalyst / trade signal**. Network-free.
- `collect_theme_event_evidence(theme, region, cfg)` (`sources/event_evidence.py`): theme-keyed collector, **DARK** when `source_event_enabled` False, bounded by `source_event_max_items`.
- Registry: `eu_ted` + `usaspending` promoted PLANNED→**enabled** event-reference sources → **28 enabled / 2 scaffolded (SEDAR+/ASX) / 5 planned (google_patents/uspto/epo_espacenet + openbb + local-language press → 29D.2/later) / 35 total**.
- Discovery council: when `source_event_enabled`, event references are threaded into `build_discovery_evidence_pack` as citeable `R#` run facts (in `evidence_ids()`) + honest gaps → discovery can cite event-trigger context (weak).
- Company report: OPTIONAL `industry_event_context` block in `report_content` (beside `industry_macro_context`), rendered only when `source_event_enabled` + a reference exists; labeled **WEAK event CONTEXT** — not company-specific, not a catalyst/materiality/trade signal, no figures. `CouncilResult.event_context` via `to_metadata_dict` (empty `[]` when off). schema/safety valid, `publication_ready` false, `human_review_required` true.
- New OFF-by-default flags `SOURCE_EVENT_ENABLED`(false) + `SOURCE_EVENT_MAX_ITEMS`(3) in `core/config.py`, **INDEPENDENT of `SOURCE_MACRO_ENABLED`**. Dark-by-default: discovery pack + report body + macro layer byte-identical when off.

## Decisions (carried)
- **New event-trigger category, parallel to the 29C macro layer** — reference-only, network-free, no API key; ONE OFF-by-default flag `SOURCE_EVENT_ENABLED` (+ `SOURCE_EVENT_MAX_ITEMS`), INDEPENDENT of the macro flag; an event reference is theme-scoped **WEAK CONTEXT** (never company-specific, never a catalyst/materiality/trade signal, never a candidate, never a recommendation); dark-by-default byte-identical when off.
- **`source_type="government_data"` (deliberately NOT "government_contract"); `ProviderType` `procurement`** — the reference points at the venue, it does NOT assert a specific contract/award exists.
- **Weak + freshness labeling** — every event reference carries `needs_human_review` + records freshness via `stale_after_days`; labeled WEAK research-priority signal.

## Deferrals (recorded — Decisions)
- **Live tender/award FETCH DEFERRED (reference-only)** — live EU TED / USAspending API fetch is a Phase 29D follow-up, mirroring the 29B.4 regulator content-fetch and 29C figure-fetch deferrals. No API keys, no report-time network.
- **Full candidate-generation-from-events deferred** — AC1 satisfied at the *context* level (discovery can cite event-trigger context); candidates still come from the curated registry.

## AC coverage (29D.1)
- AC1 discovery can cite event-trigger context (at context level; candidate-generation-from-events deferred) · AC2 event evidence source-tiered + citeable · AC3 freshness via `stale_after_days` · AC4 weak/needs-review labeling · AC5 `human_review_required=true` · AC6 no recommendation.

## Carry-forward (open — NOT a defect)
- **Coarse theme→source keyword mapping** (carried from 29C.2/29C.3) — not every event source surfaces for every theme; citation mechanism + registry are proven, refine mapping in a follow-up.
- **Standing Azure OpenAI gpt-4.1-mini TPM quota** — staging environmental limiter (partial council-agent failures under large real-data packs), NOT a code defect.

## Final staging flags (current: all 5 ON; NEW 29D.1 flag stays OFF until validated)
`LLM_COUNCIL_ENABLED` · `LLM_DISCOVERY_COUNCIL_ENABLED` · `SOURCE_CONNECTOR_ENABLED` · `SOURCE_DOCUMENT_EXTRACTION_ENABLED` · `SOURCE_MACRO_ENABLED`. **New:** `SOURCE_EVENT_ENABLED` (OFF by default — leave OFF on staging until 29D.1 is validated).

## Docs updated this checkpoint (ib-docs-agent — 29D.1 PR-open, NOT closed)
- `docs/ARCHITECTURE.md` (Status prepend for 29D.1; Source Framework layer gains `EventReferenceConnector` + `event_evidence` collector + `fetch_events`-now-live + discovery `R#` event facts + `industry_event_context` block; registry line 28/2/5/35; Phase History row `Phase 29D.1 | 🟡 PR open / pre-staging`).
- `docs/API.md` (Status prepend; `/sources/registry` + `/sources/health` show `eu_ted` + `usaspending` enabled procurement/T2; summary `enabled:28`/`scaffolded:2`/`planned:5`/`total:35`; event references + `industry_event_context` + discovery-council event citations + `SOURCE_EVENT_ENABLED` gate — NO new endpoint).
- `docs/ROADMAP.md` (Current State → Phase 29D.1 in progress / PR-open, NOT complete; 29D.2 patents + 29D.3 permits upcoming; live-fetch deferral noted).
- `docs/development/PHASE_LEDGER.md` (29D umbrella → 🟡; new 29D.1 row → 🟡 in progress, branch + PR pending; NOT ✅).
- `.env.example` (`SOURCE_EVENT_ENABLED`=false + `SOURCE_EVENT_MAX_ITEMS`=3, honest comment, no secret).
- `docs/DEPLOYMENT.md` (new "Procurement / Tender Event-Trigger Reference Layer (Phase 29D.1)" flag section — OFF by default, no new host/secret, PR-open caveat).
- `docs/development/session_state.md` (this file — overwritten).
- NOT committed — user reviews and commits.

## Next exact command / action
- **run `ib-pr-review-agent` then `gh pr create` for 29D.1; STOP at merge gate.** Do NOT merge / deploy / mark closed until human approval + staging validation is on file.
