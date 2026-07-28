# Session State — Phase 29D CLOSED (umbrella COMPLETE) · ALL OF PHASE 29 COMPLETE · Next: Phase 30A · updated 2026-07-28

> Resumable snapshot. Overwrite at each checkpoint (context-compaction skill).
> Keep decisions + evidence, not raw logs.

## Current position
- On **`main`** (latest), clean tree. Autonomous multi-phase campaign (Phase 0 → 31).
- **Phase 29D.3 — permit / regulatory-event reference connectors: ✅ CLOSED + staging-validated** (PR #66 → `main` `d567019`, VALIDATED-WITH-ENVIRONMENTAL-NOTE, backend-only, **NO migration** head `011`, **NO new flag** — reuses `SOURCE_EVENT_ENABLED` already ON, kept ON). The **LAST** Phase 29D subphase.
- **Umbrella: Phase 29D — event-trigger connectors: ✅ COMPLETE** (29D.1 procurement `a671e97` · 29D.2 patents `1c6b1c9` · 29D.3 permits `d567019`).
- **🎯 ALL OF PHASE 29 is now COMPLETE:** 29A source-registry + connector framework (`3ff96f6`) · 29B filings/regulator + primary docs/facts (29B / 29B.1 / 29B.2 / 29B.3) · 29B.4 EU/UK regulated disclosures (4A/4B/4C) · 29C macro/commodity/policy reference connectors (29C.1/29C.2/29C.3) · 29D event-trigger connectors (29D.1/29D.2/29D.3).
- **NEXT: Phase 30A — language detection + translation foundation. Stage: NOT STARTED** (a NEW capability area; no branch yet).

## Phase 29D.3 — condensed evidence (closed this session)
- Extended the reference-only **event-trigger** layer to **permits / regulatory-event venues** with **zero new wiring** (SAME generic `EventReferenceConnector`, `collect_theme_event_evidence` iterating `ALL_EVENT_SOURCES`, discovery `R#` + report `industry_event_context` paths, reuses OFF-by-default `SOURCE_EVENT_ENABLED` — no new flag/host/endpoint/migration). New **`ProviderType.permits`**; new `PERMIT_SOURCES`: **FERC** (`ferc.gov`, energy/grid/transmission/pipeline/LNG dockets), **US NRC** (`nrc.gov`, nuclear licensing), **US EPA** (`epa.gov`, environmental/emissions permitting) — all provider `permits`, **T2**, `government_data`, fixed public URLs, no API key, added as NEW enabled. Per-kind `_PERMIT_FLAVOR` thematic (bare "industrial" EXCLUDED — GICS-Industrials collision).
- Each = ONE bounded **T2 SOURCE REFERENCE** (NO docket/case/permit number/applicant/decision/outcome/date) + honest `data_not_sourced` gap + disclaimer **NO regulatory-outcome/approval/denial/materiality conclusion**; WEAK + `needs_human_review` + `stale_after_days`; network-free.
- **Folded in the 29D.2 patent-label tidy:** `_event_discovery_facts` (`discovery_council.py`) now labels run-facts per `provider_type` (procurement / patents → "patent office / index venue reference" / permits → "permit / regulatory-event venue reference").
- Registry → **34 enabled / 2 scaffolded (SEDAR+/ASX) / 2 planned (`openbb` + local-language press) / 38 total**.
- Tests: backend **2238 pass / 12 skip / 0 fail** (+28; adjacent count tests → 34/38, no ripple), ruff clean, mypy `71` baseline no-new; security PASS (pre-PR APPROVED 10/10).
- **Staging VALIDATED (2026-07-28) — VALIDATED-WITH-ENVIRONMENTAL-NOTE:** API `commit_sha=d567019` (3 stable polls), web unchanged, head `011`, AUTH_TEST_MODE absent, admin-gated, no flip. B: registry/health `ferc`+`us_nrc`+`us_epa` enabled/`permits`/T2, 34/2/2/38, honest notes, secret-free. C: "US nuclear power + grid" run (uranium UEC/UUUU) cites permit sources as reference-only weak run-fact gaps ("no live FERC permit data included") — permits = CONTEXT not a candidate. D: `safety_valid` true, no fabricated dockets/outcomes, no reco. E: per-provider label fix confirmed. F: council 7/8 (1 Azure TPM env). G: logs clean (archive through 07-27; today's build not yet archived → API surface scanned clean; 29D.3 adds no logging), 6 flags ON. Closure: `docs/development/closures/phase-29d3.md`.

## Phase 30A — scope to build next (NOT started, NEW capability area)
- **Language detection + translation FOUNDATION**, backend-first, **OFF by default**:
  - Per-**evidence-item / per-excerpt language detection**.
  - **Translation-status fields** on `EvidenceItem` (e.g. detected language, `requires_translation`, translation state).
  - A **translation-provider abstraction** (pluggable; no live provider wired on by default).
- Non-English excerpts flagged `requires_translation` — **several 29B.4 regulated-disclosure + event connectors already emit this** (French / German / Italian / Danish).
- **Always preserve original text + source URL** (translation is additive, never destructive).
- **Never translate whole documents** — bounded excerpt-level only.
- **Human-review warning** on translated evidence — never presented as an official translation.
- Same discipline: evidence-first, citation-bound, no recommendations / ratings / valuations; `human_review_required=true` / `publication_ready=false`; `/admin/*` OAuth-gated.

## Decisions (carried / standing)
- **Reference-only + deferred fetch (carried across 29B.4 / 29C / 29D):** live event/permit/docket/figure/regulator-content FETCH remains DEFERRED — no API keys, no report-time network, honest `data_not_sourced` / `primary_filing_unavailable` gaps.
- **`SOURCE_EVENT_ENABLED` KEPT ON** on staging (validation clean; reference-only / low-risk; matches keeping the other source flags on).
- **NO legal / regulatory conclusions:** a patent (29D.2) / permit (29D.3) reference is theme-scoped WEAK CONTEXT — never company-specific, never a catalyst / materiality / trade signal, never a candidate, never a recommendation, and never a legal / infringement / validity / regulatory-outcome conclusion.
- **Company→theme carry-forward (open, NOT a defect):** company-report event / macro context blocks depend on a non-thin company sector/industry (`free_real`); thin/mock profiles → collectors stay dark → blocks present-but-empty. First noted 29C.2 (also 29C.3 / 29D.1 / 29D.2). Registry + discovery-council citeability proven; refine company→theme derivation in a follow-up.
- **Azure OpenAI gpt-4.1-mini TPM quota** — standing staging environmental limiter (partial council completion under large real-data packs), NOT a code defect.
- **29D.3 log-archive lag (item G):** current-build live log tail is a follow-up (log archive lagged the 07-28 build) — mitigated by clean API surface + 29D.3 adds no logging; re-run the archived-log scan opportunistically.

## Staging flags (unchanged — all 6 ON)
`LLM_COUNCIL_ENABLED` · `LLM_DISCOVERY_COUNCIL_ENABLED` · `SOURCE_CONNECTOR_ENABLED` · `SOURCE_DOCUMENT_EXTRACTION_ENABLED` · `SOURCE_MACRO_ENABLED` · `SOURCE_EVENT_ENABLED`. DB head `011`.

## Next exact command / action
- **Scope Phase 30A (language detection + translation foundation) and create branch `feature/phase-30a-language-translation-foundation`** (from `main`). Route to `ib-product-architect` to scope, then implement backend-first, OFF-by-default. STOP at each human gate — do NOT merge / deploy / mark closed until human approval + staging validation is on file.
