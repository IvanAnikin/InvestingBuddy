# Session State — Phase 29D.3 Permit / Regulatory-Event Reference Connectors · Stage: PR (about to open) · updated 2026-07-28

> Resumable snapshot. Overwrite at each checkpoint (context-compaction skill).
> Keep decisions + evidence, not raw logs.

## Current position
- On branch **`feature/phase-29d3-permit-event-connectors`** @ **`f1a37b7`** (Phase 29D.3 committed; **PR not yet open**). Autonomous multi-phase campaign (Phase 0 → 31).
- **Phase 29D.3 — permit / regulatory-event reference connectors. Stage: PR (about to open) — pre-staging, NOT merged/deployed/validated** (the **LAST** Phase 29D subphase). Backend-only, **NO migration** (head `011`), **NO new flag/host/endpoint/secret** — reuses `SOURCE_EVENT_ENABLED`.
- **Phase 29D.2 — patents: ✅ CLOSED + staging-validated** (PR #65 → `main` `1c6b1c9`, VALIDATED-WITH-ENVIRONMENTAL-NOTE, `SOURCE_EVENT_ENABLED` already ON, kept ON).
- Umbrella: **Phase 29D — event-trigger connectors: 🟡 IN PROGRESS** (29D.1 ✅ `a671e97`; 29D.2 ✅ `1c6b1c9`; 29D.3 permits = PR-open + LAST). On 29D.3 merge + validation the umbrella is COMPLETE. Prior umbrella **Phase 29C ✅ COMPLETE**.

## Phase 29D.3 — condensed scope (backend-only, NO migration; 13 files incl. tests)
- Extends the reference-only **event-trigger** layer to **permits / regulatory-event venues** with **zero new wiring, NO new flag** (reuses `SOURCE_EVENT_ENABLED`). New **`ProviderType.permits`**; new `PERMIT_SOURCES` (into combined `ALL_EVENT_SOURCES` = procurement + patents + permits) served by the SAME generic `EventReferenceConnector` via a per-kind `_PERMIT_FLAVOR` (thematic, like patents): **FERC** (`ferc.gov`, energy/grid/transmission/pipeline/LNG dockets & permits), **US NRC** (`nrc.gov`, nuclear licensing), **US EPA** (`epa.gov`, environmental/emissions/industrial permitting) — all provider `permits`, **T2**, `source_type="government_data"`, fixed official public URLs, **no API key**, added as **NEW enabled** (not promoted from planned).
- Each emits ONE bounded **T2 SOURCE REFERENCE** (**NO specific docket/case/permit number/applicant/decision/outcome/date**) + honest `data_not_sourced` gap + explicit disclaimer that **NO regulatory-outcome/approval/denial/materiality conclusion is drawn**; WEAK + `needs_human_review` + `stale_after_days`; network-free. Themes: energy/grid/transmission/pipeline/nuclear/environmental/emissions/mining/lng/power-plant/permit/licensing — **bare "industrial" deliberately EXCLUDED** (GICS-Industrials substring collision with the 29D.1 defense path).
- **Folds in the 29D.2 follow-up tidy:** `_event_discovery_facts` (`apps/api/app/services/llm/discovery_council.py`) now labels each discovery run-fact **per `provider_type`** via `_event_provider_type` — procurement byte-identical; patents → "patent office / index venue reference"; permits → "permit / regulatory-event venue reference".
- Registry → **34 enabled / 2 scaffolded (SEDAR+/ASX) / 2 planned (`openbb` + local-language business press) / 38 total**.
- **Tests/security:** backend **2238 pass / 12 skip / 0 fail** (+28 net; adjacent registry count tests updated, no ripple); ruff clean; mypy **71** baseline (no new); security scan **PASS** (reference-only, network-free, no API keys, no regulatory-outcome conclusions, no fabricated permits).

## Decisions (carried / this phase)
- **Reference-only + deferred fetch:** live permit / docket FETCH DEFERRED (reference-only) — the keyed FERC eLibrary / EPA / NRC ADAMS APIs are NOT used; mirrors the 29B.4 / 29C / 29D.1 / 29D.2 deferrals. No API keys, no report-time network.
- **New `ProviderType.permits`:** permits get their own provider type + `_PERMIT_FLAVOR` (purely thematic, like patents), tier T2, `source_type="government_data"`.
- **Patent-label tidy folded in:** the 29D.2 non-blocking `_event_discovery_facts` cosmetic-mislabel follow-up is resolved here — run-facts now labelled per `provider_type` (procurement / patents / permits).
- **Reuse the event flag:** 29D.3 adds NO new flag — reuses the existing OFF-by-default `SOURCE_EVENT_ENABLED` / `SOURCE_EVENT_MAX_ITEMS` (INDEPENDENT of `SOURCE_MACRO_ENABLED`); `SOURCE_EVENT_ENABLED` already ON on staging — no app-setting flip expected.
- **NO legal / regulatory conclusions:** a patent (29D.2) / permit (29D.3) reference is theme-scoped **WEAK CONTEXT** — never company-specific, never a catalyst / materiality / trade signal, never a candidate, never a recommendation, and never a legal / infringement / validity / regulatory-approval conclusion.
- **Company→theme carry-forward (open, NOT a defect):** company-report event / macro context blocks depend on a non-thin company sector/industry classification (`free_real`); thin/mock profiles → collectors stay dark → blocks don't render (keys present-but-empty). First noted 29C.2 (and 29C.3 / 29D.1). Registry + discovery-council citeability are proven; refine the company→theme derivation in a follow-up.
- **Azure OpenAI gpt-4.1-mini TPM quota** — standing staging environmental limiter (partial council completion under large real-data packs), NOT a code defect.

## Staging flags (unchanged — all 6 ON)
`LLM_COUNCIL_ENABLED` · `LLM_DISCOVERY_COUNCIL_ENABLED` · `SOURCE_CONNECTOR_ENABLED` · `SOURCE_DOCUMENT_EXTRACTION_ENABLED` · `SOURCE_MACRO_ENABLED` · `SOURCE_EVENT_ENABLED`. 29D.3 reuses `SOURCE_EVENT_ENABLED` (already ON) — no new app setting.

## Next exact command / action
- **Run `ib-pr-review-agent`, then `gh pr create` for Phase 29D.3** (branch `feature/phase-29d3-permit-event-connectors` @ `f1a37b7` → `main`). **STOP at the merge gate** — do NOT merge / deploy / mark closed until human approval + staging validation is on file. After 29D.3 validates, the whole Phase 29D umbrella is COMPLETE → next is **Phase 30A (language detection + translation foundation)** (then wider Phase 30 translation / local-language + PDF table extraction / OCR).
