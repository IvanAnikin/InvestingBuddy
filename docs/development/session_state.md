# Session State — Phase 29B.4C IN PROGRESS · stage: PR (about to open) (updated 2026-07-27)

> Resumable snapshot for the current Claude Code session. Overwrite this file at
> each checkpoint (see the `context-compaction` skill). Keep decisions + evidence,
> not raw logs.

## Current position
- On branch `feature/phase-29b4c-swiss-nordic-de-disclosures`, HEAD `4eb4054`.
- **Phase 29B.4C — Swiss / Nordic / Germany regulated-disclosure connectors: 🟡 IN PROGRESS.**
  Stage: **PR about to open (pre-staging).** NOT merged, NOT deployed, NOT validated —
  do NOT mark ✅.
- Backend-only, **11 files incl. tests**, **NO migration** (still DB head `011`), no new
  flag/host/endpoint.
- Umbrella **Phase 29B.4** stays 🟡 until 4C is validated: 4A ✅ (#58 `5138725`),
  4B ✅ (#59 `1d97612`), **4C in review**. **4C is the LAST 29B.4 subphase** — once it
  merges + validates the whole 29B.4 umbrella completes and **Phase 29C (macro/commodity/
  policy)** is next.

## Phase 29B.4C — what was built (verified GREEN, pre-staging)
Three new dedicated regulator connectors following the same **T2 venue-reference + honest
`primary_filing_unavailable` content-gap, network-free, verified-issuer-gated** pattern as
29B.4A/4B:
- **`DeutscheBoerseConnector`** (`app/services/sources/connectors/deutsche_boerse.py`, promotes
  the `deutsche_boerse` scaffold): `SAP.DE` (Germany / Xetra) → ONE bounded **T2 reference** to
  the German regulated-info venue (Bundesanzeiger / Deutsche Börse / BaFin), fixed URL
  `https://www.bundesanzeiger.de`, + German `requires_translation` (`GapType.translation_required`).
- **`NordicDisclosuresConnector`** (`connectors/nordic_disclosures.py`, promotes the
  `nordic_disclosures` scaffold): `PNDORA.CO` (Denmark / Nasdaq Copenhagen) → **T2 reference**
  (Nasdaq Nordic company news `https://www.nasdaqomxnordic.com/news/companynews` / Finanstilsynet)
  + Danish `requires_translation` (generalizes to ST / HE / OL Nordic venues).
- **`SixSwissConnector`** (`connectors/six_swiss.py`, **NEW `six_swiss` source — no Swiss scaffold
  existed**; resolves the honest Swiss/SIX gap the 4B plan flagged): `CFR.SW` Richemont + `UHR.SW`
  Swatch on SIX (SW / VX) → **T2 reference** (SIX Swiss Exchange / SIX Exchange Regulation official
  notices `https://www.six-group.com/…/official-notices.html`) + honest content gap. **NO
  `requires_translation` claim** — Switzerland is multilingual and major issuers publish English;
  only a neutral DE/FR/IT multilingual note in warnings.

Wiring:
- `company_evidence.py`: extended exchange/country→regulator mapping (DE/Xetra/Frankfurt+Germany
  → `deutsche_boerse`; CO/Nasdaq-Copenhagen+Denmark → `nordic_disclosures`; SW/VX/SIX+Switzerland
  → `six_swiss`) + `REGULATOR_REFERENCE_IDS` + tightened `_relevant_scaffold_ids`.
- `registry.py`: `deutsche_boerse` + `nordic_disclosures` promoted out of the scaffold table;
  `six_swiss` added as a **NEW enabled `regulator` / `T2_regulator_or_gov`** source →
  **registry now 11 enabled / 2 scaffolded** (remaining scaffolds: **SEDAR+, ASX only**).
- Adjacent test updates for the promotions / counts (no production ripple).

## Tests / checks (GREEN)
- Backend **2071 pass / 12 skip / 0 fail**; ruff clean; mypy `71` baseline (no new); security PASS.
- No web change (backend-only).

## Decisions
- **SIX Swiss carries NO `requires_translation`.** Switzerland is multilingual (DE/FR/IT) and
  major SIX issuers publish English annual reports, so a blanket translation-required marker would
  be misleading. Only a neutral DE/FR/IT multilingual note in `warnings` — honest, not a
  translation claim. (Germany → German, Nordic → Danish DO carry `requires_translation`.)
- **Live regulator-venue CONTENT fetch DEFERRED (carried from 29B.4A/4B).** Shipped posture is the
  honest T2 venue *reference* + explicit `primary_filing_unavailable` content gap at report time;
  live content fetch is a future follow-up (not this subphase).
- **`requires_translation` is an honesty marker, not a translation** (pending Phase 30).
- One region per subphase: 4A UK ✅, 4B Euronext ✅, 4C Swiss/Nordic/Germany (in review).

## Staging flags (unchanged — all ON, KEEP)
`LLM_COUNCIL_ENABLED`=true · `LLM_DISCOVERY_COUNCIL_ENABLED`=true ·
`SOURCE_CONNECTOR_ENABLED`=true · `SOURCE_DOCUMENT_EXTRACTION_ENABLED`=true

## Carry-forward limitations
1. All regulated-disclosure connectors (UK FCA NSM, Euronext, Deutsche Börse, Nordic, SIX Swiss)
   emit a T1-filing-content gap — venue *reference* + honest content gap only (live content fetch
   deferred).
2. SEDAR+ / ASX remain scaffolded (honest gaps) — later batches.
3. No local-language translation yet — DE/DK/other non-English primary docs flagged
   `requires_translation` pending Phase 30 (SIX Swiss deliberately excluded — multilingual, English
   available).
4. (From 29B.3) primary-fact happy path is unit-fixture-proven only; scoring/completeness numeric
   uplift is capability-only (not wired to a production caller).

## Next exact command / action
- **Run `ib-pr-review-agent`, then `gh pr create` for 29B.4C** (branch
  `feature/phase-29b4c-swiss-nordic-de-disclosures`, HEAD `4eb4054`).
- **STOP at the merge gate** — do NOT merge/deploy/validate/close without human approval +
  staging validation evidence (merge SHA + deployed SHA + validation result).
