# Session State — Phase 29D.2 CLOSED · Next: Phase 29D.3 Permit / Regulatory-Event Reference Connectors · Stage: not-started · updated 2026-07-28

> Resumable snapshot. Overwrite at each checkpoint (context-compaction skill).
> Keep decisions + evidence, not raw logs.

## Current position
- On **`main`** @ **`1c6b1c9`** (Phase 29D.2 squash-merged). Clean tree. Autonomous multi-phase campaign (Phase 0 → 31).
- **Phase 29D.2 — patent event-trigger reference connectors: ✅ CLOSED + staging-validated (VALIDATED-WITH-ENVIRONMENTAL-NOTE)** — PR #65 → `main` `1c6b1c9`, `SOURCE_EVENT_ENABLED` already ON (kept ON), NO migration (head `011`).
- **Active: Phase 29D.3 — permit / regulatory-event reference connectors. Stage: NOT STARTED** (the **LAST** Phase 29D subphase).
- Umbrella: **Phase 29D — event-trigger connectors: 🟡 IN PROGRESS** (29D.1 ✅ `a671e97`; 29D.2 ✅ `1c6b1c9`; 29D.3 permits = next + LAST). Prior umbrella **Phase 29C ✅ COMPLETE** (`a8ac580` + `80c8454` + `ad6dde5`).

## Phase 29D.2 — condensed closure evidence (backend-only, NO migration)
- Extended the 29D.1 reference-only **event-trigger** layer to **patents** with **zero new wiring, NO new flag** (reuses `SOURCE_EVENT_ENABLED`). New `PATENT_SOURCES` (into combined `ALL_EVENT_SOURCES`) served by the SAME generic `EventReferenceConnector`: **Google Patents** (`patents.google.com`, T5, provider `patents`), **USPTO** (`uspto.gov`, T2, provider `patents`), **EPO Espacenet** (`worldwide.espacenet.com`, T2, provider `patents`) — all promoted PLANNED→**enabled**. Per-kind `_EventFlavor` = purely thematic (innovation/R&D/patent/IP/tech/semi/pharma/battery/EV/materials), `source_type="government_data"`; ONE bounded T2/T5 SOURCE REFERENCE (fixed official URL, no API key, NO specific patent number/title/inventor/assignee/claim/date) + honest `data_not_sourced` gap + explicit **NO legal/infringement/validity/patentability/ownership conclusion** disclaimer; WEAK + `needs_human_review`; network-free. Registry → **31 enabled / 2 scaffolded (SEDAR+/ASX) / 2 planned (`openbb` + local-language business press) / 35 total**.
- **Tests/security:** backend **2210 pass / 12 skip / 0 fail** (+26; adjacent count tests → 31/2, no ripple); ruff clean; mypy **71** baseline (no new); security PASS (reference-only, network-free, no API keys, no legal/infringement conclusions); pre-PR review APPROVED 10/10.
- **Staging (2026-07-28) VALIDATED-WITH-ENVIRONMENTAL-NOTE:** API `commit_sha=1c6b1c9` (3 stable polls), web unchanged, head `011`, AUTH_TEST_MODE absent, admin-gated; no app-setting flip (`SOURCE_EVENT_ENABLED` already ON). **B:** registry/health show the 3 patent sources enabled (T5/T2/T2), 31/2/2/35, honest "live filings not fetched; no legal/infringement conclusions; WEAK" notes, secret-free. **C:** a "semiconductor innovation" run (3 real semis incl. **AMAT**) cites Google Patents/USPTO/Espacenet (×11 each) as reference-only `R#` facts + honest "not fetched" gaps; patents = CONTEXT not a candidate. **D:** no fabricated patent numbers, no legal/validity conclusions, forbidden terms only negated; `safety_valid` true. **F:** council `llm_used`, `completed_with_warnings` (Azure TPM ENVIRONMENTAL). **G:** 6 flags ON.

## ⚠️ Follow-up recorded for 29D.3 (patent-label tidy — NON-BLOCKING, NOT a defect)
- At `1c6b1c9`, **`_event_discovery_facts`** (`apps/api/app/services/llm/discovery_council.py`) labels **patent** event references with generic **"procurement/tender venue reference (T2)"** boilerplate — a **cosmetic mislabel** in the discovery-pack narration. NOT a safety issue (no fabrication, no legal conclusion; patent-specific honesty carried by the gap messages + registry `reliability_note`). **FIX = label per `provider_type` (procurement vs patents); FOLD INTO Phase 29D.3.**

## ⚠️ az-log-timeout note (operational, recorded — NOT a defect)
- Staging **log secret-scan UNKNOWN** — the `az` log download **timed out >90s** this session. Mitigated: (1) API response surface scanned clean; (2) **29D.2 adds no logging** (reference-only, keyless). Re-run opportunistically.

## Decisions (carried / this phase)
- **Reference-only + deferred fetch:** live patent-filing FETCH DEFERRED (reference-only) — a Phase 29D follow-up, mirroring the 29B.4 / 29C / 29D.1 deferrals. No API keys, no report-time network.
- **Reuse the event flag:** 29D.2 added NO new flag — reuses the existing OFF-by-default `SOURCE_EVENT_ENABLED` / `SOURCE_EVENT_MAX_ITEMS` (INDEPENDENT of `SOURCE_MACRO_ENABLED`); `SOURCE_EVENT_ENABLED` KEPT ON on staging. 29D.3 should reuse the SAME event layer (no new flag).
- **NO legal / regulatory conclusions:** a patent (29D.2) / permit (29D.3) reference is theme-scoped **WEAK CONTEXT** — never company-specific, never a catalyst / materiality / trade signal, never a candidate, never a recommendation, and never a legal / infringement / validity / regulatory-approval conclusion.
- **Company→theme carry-forward (open, NOT a defect):** company-report event / macro context blocks depend on a non-thin company sector / industry classification (`free_real`) to derive a theme; thin / mock profiles → collectors stay dark → blocks don't render (keys present-but-empty). First noted **29C.2** (and 29C.3 / 29D.1). Registry + discovery-council citeability are proven; refine the company→theme derivation in a follow-up.
- **Azure OpenAI gpt-4.1-mini TPM quota** — standing staging environmental limiter (partial council completion under large real-data packs), NOT a code defect.

## Staging flags (unchanged — all 6 ON)
`LLM_COUNCIL_ENABLED` · `LLM_DISCOVERY_COUNCIL_ENABLED` · `SOURCE_CONNECTOR_ENABLED` · `SOURCE_DOCUMENT_EXTRACTION_ENABLED` · `SOURCE_MACRO_ENABLED` · `SOURCE_EVENT_ENABLED`. 29D.3 should reuse `SOURCE_EVENT_ENABLED` (already ON) — no new app setting expected.

## Next exact command / action
- **Scope Phase 29D.3 (permits / regulatory-event connectors) and create branch `feature/phase-29d3-permit-event-connectors`.** Extend the event layer with permit / regulatory-event *references* (energy / mining / grid / industrial permits where safe) — metadata-first, honest gaps, **no fake permits**, reference-only, reuse `SOURCE_EVENT_ENABLED`, no legal / regulatory-approval conclusions. **Also FOLD IN** the `_event_discovery_facts` per-provider label tidy (procurement vs patents vs permits). It is the **LAST 29D subphase** — after it, Phase 30 (translation / local-language + PDF table extraction / OCR). **STOP at the merge gate** — do not merge / deploy / mark closed until human approval + staging validation is on file.
