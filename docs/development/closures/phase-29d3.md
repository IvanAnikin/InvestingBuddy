# Closure Report — Phase 29D.3: Permit / Regulatory-Event Reference Connectors (FERC / US NRC / US EPA)

> Produced ONLY after merge + deploy + staging validation. All SHAs and
> validation results below are real and verified this session (2026-07-28).
>
> **Third and LAST subphase of the Phase 29D umbrella** (event-trigger
> connectors). Its closure **COMPLETES the entire Phase 29D umbrella**
> (procurement + patents + permits) **and with it ALL OF PHASE 29.**

- **PR:** #66 "Phase 29D.3: add permit/regulatory-event reference connectors (FERC/NRC/EPA)" — squash-merged to `main`.
- **Merge SHA:** `d567019dd8ee69a7ed11c434a4e563656b765561`
- **API SHA** (`GET /health` `commit_sha`): `d567019` — matches merge SHA? yes (3 consecutive stable polls).
- **Web SHA** (`GET /api/version` `commit_sha`): unchanged — **expected**: backend-only PR, no web change this subphase.
- **Deploy:** "Deploy API — Staging" success at `d567019`. No web deploy (no web change).
- **Migration:** none — DB head `011` (unchanged).
- **AUTH_TEST_MODE:** absent — confirmed (protected routes challenge, not bypassed).
- **Tests:** backend **2238 pass / 12 skip / 0 fail** (+28 net; new `test_phase29d3`; adjacent registry count assertions updated to **34 enabled / 38 total** for the three permit additions — no functional ripple), ruff clean, mypy `71` pre-existing baseline (no new). Frontend N/A (backend-only).
- **Security / review:** ib-security-agent PASS (reference-only, network-free at report time; **no API keys**; **NO regulatory-outcome / approval / denial / materiality conclusion** in any product text; no fabricated docket / case / permit number / applicant / decision / date; **WEAK** + `needs_human_review` labeling; reuses the existing event flag — no new flag; no recommendation / rating / valuation output). Pre-PR review APPROVED (10/10).

## What 29D.3 shipped (backend-only, NO migration)
Extends the 29D.1/29D.2 reference-only **event-trigger** layer to **permits /
regulatory-event venues** — **reference-only + OFF by default, zero new wiring,
NO new flag** (reuses `SOURCE_EVENT_ENABLED`).

- **New `PERMIT_SOURCES` table** added into the combined `ALL_EVENT_SOURCES`
  (= procurement + patents + permits), served by the SAME generic
  **`EventReferenceConnector`** (`sources/connectors/event_reference.py`) over three
  official public regulatory venues — **FERC** (`ferc.gov`; energy / grid /
  transmission / pipeline / LNG dockets & permits), **US NRC** (`nrc.gov`; nuclear
  reactor licensing / permits) and **US EPA** (`epa.gov`; environmental / emissions /
  industrial permitting) — all provider **`permits`** (a NEW `ProviderType.permits`),
  **T2**, `source_type="government_data"`, at fixed official public URLs (**no API
  key**), added as **NEW enabled** sources (not promoted from planned).
- A per-kind **`_PERMIT_FLAVOR`** makes permit references **PURELY THEMATIC**
  (energy / grid / transmission / pipeline / nuclear / environmental / emissions /
  mining / lng / power-plant / permit / licensing — **bare "industrial" deliberately
  EXCLUDED** to avoid a GICS-Industrials substring collision with the 29D.1 defense
  path). Each `fetch_events` emits, per relevant theme, ONE bounded **T2 SOURCE
  REFERENCE** (fixed official public URL, **no API key**, **NO specific docket /
  case / permit number / applicant / decision / outcome / date**) + an honest
  `data_not_sourced` gap + an explicit disclaimer that **NO regulatory-outcome /
  approval / denial / materiality conclusion is drawn**. Each is **WEAK internal
  research-priority CONTEXT only** + `needs_human_review` + `stale_after_days`
  freshness; network-free.
- **Folds in the 29D.2 follow-up tidy:** `_event_discovery_facts`
  (`apps/api/app/services/llm/discovery_council.py`) now labels each discovery
  run-fact **per `provider_type`** — procurement byte-identical; patents →
  "patent office / index venue reference"; permits → "permit / regulatory-event
  venue reference" — resolving the 29D.2 cosmetic patent-label mislabel.
- **Reuses** the SAME collector (`collect_theme_event_evidence` iterates
  `ALL_EVENT_SOURCES`), the discovery-council `R#` event-citation path, the
  `industry_event_context` report block, and the existing `SOURCE_EVENT_ENABLED`
  flag — **no new flag / host / endpoint / migration**.
- **Registry:** `ferc` + `us_nrc` + `us_epa` added as **NEW enabled**
  event-reference sources (`permits` provider) → registry now **34 enabled /
  2 scaffolded (SEDAR+ / ASX) / 2 planned (`openbb` + local-language business
  press) / 38 total**.

## Staging validation — VALIDATED-WITH-ENVIRONMENTAL-NOTE

No app-setting flip this subphase — **`SOURCE_EVENT_ENABLED` was already ON from
29D.1**, so 29D.3 was validated directly in the ON state.

- **B — registry / health (VALIDATED):** `/sources/registry` + `/sources/health`
  show **`ferc`** + **`us_nrc`** + **`us_epa`** all **enabled** (`permits` provider,
  **T2**); summary **34 enabled / 2 scaffolded / 2 planned / 38 total**; honest
  **"permit / regulatory-event venue reference; live dockets not fetched; no
  regulatory-outcome conclusions; WEAK"** notes; **secret-free**.
- **C — discovery (VALIDATED):** a **"US nuclear power + grid"** discovery run
  (uranium candidates **UEC / UUUU**) cites the permit sources as **reference-only
  WEAK** run-fact gaps ("no live FERC permit data included"). Permits are **CONTEXT,
  NOT a candidate** — this **proves ON-state citeability**.
- **D — safety (VALIDATED):** `safety_valid` true; **no fabricated dockets /
  outcomes**, **no regulatory-outcome conclusion**, **no recommendation**;
  `publication_ready` false, `human_review_required` true.
- **E — per-provider label fix (VALIDATED):** permit run-facts are labelled
  **"permit / regulatory-event venue reference"** (not the old procurement / tender
  boilerplate) — the folded-in 29D.2 patent-label tidy is confirmed done.
- **F — council:** **7/8** agents (the 1 failure = **Azure OpenAI gpt-4.1-mini TPM
  throttling = ENVIRONMENTAL**, not a code defect).
- **G — operational:** logs clean (archive through 07-27; today's build runtime not
  yet archived → the **API response surface was scanned clean**; **29D.3 adds no
  logging**), AUTH_TEST_MODE absent, publication admin-gated, **6 flags ON**.

### ⚠️ Minor note (recorded — item G, operational, NOT a defect)
The current-build **live log tail** is a follow-up: the staging **log archive lagged
behind the 07-28 build**, so the newest runtime lines were not yet archived at
validation time. Mitigations that make this **non-blocking**: (1) the **API response
surface was scanned clean** (registry / health / discovery responses are secret-free);
(2) **29D.3 adds no logging** — the connector is reference-only, network-free, keyless,
so it introduces no new log line or secret path. Re-run the archived-log secret-scan
opportunistically once the archive catches up to the 07-28 build.

## Deliberate deferrals (recorded)
- **Live permit / docket FETCH is DEFERRED (reference-only)** — the keyed FERC
  eLibrary / EPA / NRC ADAMS APIs are NOT used; a live permit / docket fetch is a
  documented follow-up, mirroring the 29B.4 regulator content-fetch, the 29C
  figure-fetch and the 29D.1 / 29D.2 event-fetch deferrals. No API keys, no
  report-time network, evidence-first, honest `data_not_sourced` gaps.

## Limitations (honest — carry-forward candidates)
1. **No live permit / docket data** — the layer emits a T2 SOURCE REFERENCE +
   honest `data_not_sourced` gap only; no specific docket / case / permit number /
   applicant / decision / outcome / date is fetched (deferred, reference-only; see
   above).
2. **Permit references are theme / industry CONTEXT only** — never company-specific,
   never a catalyst / materiality / trade signal, never a candidate, never a
   recommendation, and **never a regulatory-outcome / approval / denial conclusion**;
   WEAK + `needs_human_review`.
3. **Company→theme derivation from a thin / coarse company profile under-surfaces
   the event / macro context blocks** in company reports (present-but-empty when
   `free_real` returns no sector / industry). Known limitation, not a defect — same
   coarse / thin company→theme carry-forward first noted in **29C.2** (and 29C.3 /
   29D.1 / 29D.2). Proven citeable at the registry + discovery-council level.
4. **Azure OpenAI gpt-4.1-mini TPM quota** remains a standing staging environmental
   limiter (partial council completion this run — 7/8), not a code defect.
5. **Current-build live log secret-scan not re-run** (log archive lagged the 07-28
   build) — mitigated by a clean API response surface + 29D.3 adding no logging (see
   the minor note above).

## Decision (recorded)
- **`SOURCE_EVENT_ENABLED` KEPT ON on staging** (already ON from 29D.1; validation
  clean; reference-only / low-risk; matches keeping `SOURCE_MACRO_ENABLED` /
  `SOURCE_CONNECTOR_ENABLED` / `SOURCE_DOCUMENT_EXTRACTION_ENABLED` on).

## Final flags (kept on staging — all 6 ON, UNCHANGED by 29D.3)
`LLM_COUNCIL_ENABLED`=on · `LLM_DISCOVERY_COUNCIL_ENABLED`=on · `SOURCE_CONNECTOR_ENABLED`=on ·
`SOURCE_DOCUMENT_EXTRACTION_ENABLED`=on · `SOURCE_MACRO_ENABLED`=on · `SOURCE_EVENT_ENABLED`=on.
29D.3 reuses `SOURCE_EVENT_ENABLED` (already ON) — **no new app setting**.

## Final verdict
**CLOSED + validated (VALIDATED-WITH-ENVIRONMENTAL-NOTE)** — merged (`d567019`),
deployed (API at `d567019`, 3 stable polls; web unchanged by design), staging-validated
ON-state (registry / health show `ferc` / `us_nrc` / `us_epa` enabled `permits` / T2;
the "US nuclear power + grid" discovery run cites the permit sources as reference-only
WEAK run-fact gaps; safety valid; no fabricated dockets / outcomes; no regulatory-outcome
conclusion; the per-provider run-fact label fix confirmed). No DB migration (head `011`).
Environmental notes: (1) partial council completion (7/8) = Azure OpenAI TPM throttling;
(2) current-build live log secret-scan not re-run (log archive lagged the 07-28 build) —
mitigated. Safety posture intact: evidence-first, citation-bound, no recommendation /
valuation output, admin-gated routes, human approval before publication.

## Umbrella status — Phase 29D COMPLETE ✅
- **29D.1 procurement / tenders** — PR #64 `a671e97` ✅ (closure: `phase-29d1.md`)
- **29D.2 patents** — PR #65 `1c6b1c9` ✅ (closure: `phase-29d2.md`)
- **29D.3 permits / regulatory-event venues** — PR #66 `d567019` ✅ (this report)

## 🎯 ALL OF PHASE 29 is now COMPLETE
- **29A** — source registry + connector framework (`3ff96f6`).
- **29B / 29B.1 / 29B.2 / 29B.3** — filings & regulator connectors + non-US company
  IR evidence + primary-document text extraction + primary-fact integration.
- **29B.4 (4A / 4B / 4C)** — EU / UK regulated-disclosure connectors.
- **29C (29C.1 / 29C.2 / 29C.3)** — macro / commodity-energy / policy-government
  reference connectors.
- **29D (29D.1 / 29D.2 / 29D.3)** — procurement / patent / permit event-trigger
  reference connectors.

Metadata-first, honest gaps, **no fake awards / contracts / permits, no legal /
regulatory-outcome conclusions, no broad crawling** — an event is an **internal
research-priority evidence signal only, never a recommendation**.

## Next
- **Phase 30A — language detection + translation foundation** (a NEW capability
  area): per-evidence-item / per-excerpt language detection + translation-status
  fields on `EvidenceItem` + a translation-provider abstraction, **OFF by default**;
  French / German / Italian / Danish excerpts flagged `requires_translation` (already
  emitted by several 29B.4 / event connectors); **preserve original text + source
  URL**; **never translate whole documents** (bounded excerpt-level only); a
  **human-review warning** on translated evidence (never presented as an official
  translation).
