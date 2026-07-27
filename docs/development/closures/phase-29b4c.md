# Closure Report — Phase 29B.4C: Swiss / Nordic / Germany Regulated-Disclosure Connectors

> Produced ONLY after merge + deploy + staging validation. All SHAs and
> validation results below are real and verified this session (2026-07-27).
>
> **This subphase completes the entire Phase 29B.4 umbrella** (EU/UK
> regulated-disclosure connectors: 4A UK FCA NSM + 4B Euronext + 4C
> Swiss/Nordic/Germany). See the umbrella note at the end.

- **PR:** #60 "Phase 29B.4C: add Swiss/Nordic/Germany regulated-disclosure connectors" — squash-merged to `main`.
- **Merge SHA:** `de126ee66b1242f336f57c0ae2a9f31a1f7941d9`
- **API SHA** (`GET /health` `commit_sha`): `de126ee` — matches merge SHA? yes (3 consecutive stable polls)
- **Web SHA** (`GET /api/version` `commit_sha`): unchanged — **expected**: backend-only PR, no web change this subphase (web unchanged since Phase 29B.2, `793e0a7`).
- **Deploy:** "Deploy API — Staging" success at `de126ee`. No web deploy (no web change).
- **Migration:** none — DB head `011` (unchanged).
- **AUTH_TEST_MODE:** absent — confirmed (protected routes challenge, not bypassed).
- **Tests:** backend **2071 pass / 12 skip / 0 fail** (+16 new in `apps/api/tests/test_phase29b4c*`; adjacent scaffold-count tests updated for the three promotions, no ripple), ruff clean, mypy `71` pre-existing baseline (no new). Frontend N/A (backend-only).
- **Security / review:** ib-security-agent PASS (network-free at report time; no fabricated filing / headline / date / notice number; fixed public HTTPS venue URLs with no query/secret; verified-issuer-gated; honest gaps; six_swiss carries **no** translation claim). Pre-PR review APPROVED (10/10).

## What 29B.4C shipped (backend-only, NO migration)
Three new dedicated regulator connectors following the same **promote-scaffold→connector /
T2 venue *reference* + honest `primary_filing_unavailable` content gap, network-free at report
time, verified-issuer-gated** pattern proven in 29B.4A (UK) and 29B.4B (Euronext) — resolved via
`verified_issuer_sources`, **never a fabricated filing / headline / date / notice number**:

- **`DeutscheBoerseConnector`** (`apps/api/app/services/sources/connectors/deutsche_boerse.py`,
  promotes the `deutsche_boerse` scaffold) — `SAP.DE` SAP (Germany / Xetra) → ONE bounded **T2
  reference** to the German regulated-information venue (Bundesanzeiger / Deutsche Börse / BaFin)
  at the fixed public URL `https://www.bundesanzeiger.de` + German **`requires_translation`**
  (`GapType.translation_required`) + honest content gap.
- **`NordicDisclosuresConnector`** (`connectors/nordic_disclosures.py`, promotes the
  `nordic_disclosures` scaffold) — `PNDORA.CO` Pandora (Denmark / Nasdaq Copenhagen) → **T2
  reference** (Nasdaq Nordic company news `https://www.nasdaqomxnordic.com/news/companynews` /
  Finanstilsynet) + Danish **`requires_translation`** (generalizes to the ST / HE / OL Nordic
  venues) + honest content gap.
- **`SixSwissConnector`** (`connectors/six_swiss.py`, a **NEW `six_swiss` source built from
  scratch — no Swiss scaffold existed**, resolving the honest Swiss/SIX gap the 29B.4B plan
  flagged) — `CFR.SW` Richemont + `UHR.SW` Swatch on SIX (SW / VX) → **T2 reference** (SIX Swiss
  Exchange / SIX Exchange Regulation official notices) + honest content gap, with **NO
  `requires_translation` claim** — Switzerland is multilingual and major issuers publish English,
  so only a neutral DE/FR/IT multilingual note in `warnings` (honest, not a translation claim).

`company_evidence.py` extends the exchange/country→regulator mapping (DE/Xetra/Frankfurt+Germany →
`deutsche_boerse`; CO/Nasdaq-Copenhagen+Denmark → `nordic_disclosures`; SW/VX/SIX+Switzerland →
`six_swiss`) + `REGULATOR_REFERENCE_IDS` + tightened `_relevant_scaffold_ids`. `registry.py`
promotes `deutsche_boerse` + `nordic_disclosures` out of the scaffold table and registers
`six_swiss` as a **new enabled `regulator` / `T2_regulator_or_gov`** source, so `/sources/registry`
+ `/sources/health` show all three enabled and the summary becomes **11 enabled / 2 scaffolded**
(remaining scaffolds: **SEDAR+, ASX only**). Non-eligible / unresolvable issuers → honest
`source_not_eligible` gap.

## Validation A–I
- A — API SHA converged (3 consecutive matches at `de126ee`): pass
- B — Web SHA: n/a — no web change this PR (web stays `793e0a7`, as designed): pass
- C — registry/health: `/sources/registry` + `/sources/health` show `deutsche_boerse` +
  `nordic_disclosures` + `six_swiss` all **enabled** `regulator` / `T2_regulator_or_gov`, summary
  **11 enabled / 2 scaffolded** (only `sedar_plus` + `asx` remain), honest "content not fetched at
  report time" notes, `six_swiss` asserts **NO** translation, secret-free: pass
- D — Germany (`SAP.DE`, Xetra): resolves to SAP, emits ONE `deutsche_boerse` **T2 venue
  reference** (`bundesanzeiger.de`) + German `requires_translation` + honest content gap;
  `company_ir` evidence still present: pass
- E — Nordic (`PNDORA.CO`, Nasdaq Copenhagen): emits ONE `nordic_disclosures` **T2 venue
  reference** (`nasdaqomxnordic.com`) + Danish `requires_translation` + honest content gap;
  `company_ir` evidence still present: pass
- F — Switzerland (`CFR.SW` Richemont + `UHR.SW` Swatch, SIX): emit ONE `six_swiss` **T2 venue
  reference** with `requires_translation`=**false** (no `six_swiss` translation gap; the
  `translation_required` gap that is present is the **pre-existing `company_ir`'s**, honestly
  attributed); `company_ir` evidence still present: pass
- G — no-regression / no leakage: `BA.LSE` still maps to `uk_fca_nsm` (no DE/Nordic/Swiss item),
  BA → BAE Systems **never Boeing**; `MC.PA` still maps to `euronext` (no DE/Nordic/Swiss
  leakage); AAPL guardrail — the three new connectors → `source_not_eligible`, **0 items** for a
  US issuer: pass
- H — logs / no-secrets: current-build logs clean; the sole `api_token=` match is the **known
  2026-07-22 historical** leak (pre-hotfix), not a current-build regression; AUTH_TEST_MODE
  absent: pass
- I — safety/publication: `schema_valid=true`, `safety_valid=true`, `publication_ready=false`,
  `human_review_required=true`; final flags KEPT ON; publication admin-gated (no recommendation /
  valuation output): pass

## Staging validation verdict
- **VALIDATED (full).** All three connectors are provably enabled, network-free at report time,
  bounded, safety-gated and fabrication-free; Germany (`requires_translation` German), Nordic
  (`requires_translation` Danish) and Switzerland (**no** translation claim) issuers each gain an
  honest T2 venue reference + explicit content gap; UK / Euronext / US issuers are unchanged (no
  leakage).

## Deliberate deferral (recorded — carried forward across all 29B.4 connectors)
- **Live regulator-venue CONTENT fetch is DEFERRED (deliberate, carried forward from 29B.4A/4B
  and now across the whole 29B.4 umbrella).** A bounded server-side fetch of a regulator venue
  reads essentially nothing (SPA / index) while adding external-regulator fetch surface. The
  shipped posture is the **honest T2 venue reference + explicit `primary_filing_unavailable`
  content gap** at report time (reference-only). Live content fetch is a future follow-up.

## Limitations (honest — carry-forward candidates)
1. **DE / Nordic / Swiss (and Euronext, UK FCA NSM) T1 filing CONTENT is not fetched** — the
   connectors emit a venue *reference* + honest content gap only (deferred, reference-only across
   all 29B.4 connectors; see above).
2. Regulator connectors **SEDAR+ (CA) and ASX (AU)** remain **scaffolded** (honest gaps) — a
   later batch.
3. No local-language translation yet — German / Danish / other non-English primary docs flagged
   `requires_translation` pending Phase 30; Switzerland carries only a neutral multilingual note,
   not a translation claim.
4. (From 29B.3) the primary-fact happy path is unit-fixture-proven only; the
   scoring/completeness numeric uplift is capability-only (not wired to a production caller).

## Final flags (kept on staging — all ON, unchanged)
`LLM_COUNCIL_ENABLED`=on · `LLM_DISCOVERY_COUNCIL_ENABLED`=on ·
`SOURCE_CONNECTOR_ENABLED`=on · `SOURCE_DOCUMENT_EXTRACTION_ENABLED`=on

## Final verdict
**CLOSED + validated** — merged (`de126ee`), deployed (API at `de126ee`, 3 stable polls; web
unchanged at `793e0a7` by design), staging-validated (A–I PASS, full). No DB migration (head
`011`). Safety posture intact: evidence-first, citation-bound, no recommendation / valuation
output, admin-gated routes, human approval before publication.

## Umbrella closure — Phase 29B.4 COMPLETE
Phase 29B.4C is the **final subphase** of the Phase 29B.4 umbrella (EU/UK regulated-disclosure
connectors). With 4C merged + deployed + staging-validated, the **whole 29B.4 umbrella is now
COMPLETE**:

- **4A UK FCA NSM/RNS** — PR #58 `5138725` (closure `phase-29b4a.md`)
- **4B Euronext** — PR #59 `1d97612` (closure `phase-29b4b.md`)
- **4C Swiss / Nordic / Germany** — PR #60 `de126ee` (this report)

Registry stands at **11 enabled / 2 scaffolded** (only SEDAR+/ASX regulator connectors remain
scaffolded, deferred to a later batch). Ledger (`docs/development/PHASE_LEDGER.md`) and roadmap
(`docs/ROADMAP.md`) updated to reflect both the 4C closure and the umbrella completion.

**Next phase: Phase 29C** (macro / commodity / policy evidence connectors — likely split into
29C.1 macro baseline / 29C.2 commodity + energy / 29C.3 policy + government; evidence-first, no
recommendations / valuations, prefer official / government sources).
