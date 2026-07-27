# Session State — Phase 29B.4B CLOSED · next: Phase 29B.4C · stage: not-started (updated 2026-07-27)

> Resumable snapshot for the current Claude Code session. Overwrite this file at
> each checkpoint (see the `context-compaction` skill). Keep decisions + evidence,
> not raw logs.

## Current position
- On `main`, HEAD `1d97612`, clean tree.
- **Phase 29B.4B — Euronext regulated-disclosure connector: ✅ CLOSED** (merged + deployed +
  staging-validated). PR #59 squash-merged to `main` at `1d97612`; API `commit_sha=1d97612`
  (3 stable polls); web unchanged `793e0a7` (backend-only); DB head `011` (no migration).
  Closure report: `docs/development/closures/phase-29b4b.md`.
- **Next: Phase 29B.4C — Swiss / Nordic / Germany regulated-disclosure connectors. Stage:
  not-started (no branch yet).** The final planned subphase of the Phase 29B.4 umbrella.
- Umbrella **Phase 29B.4** stays 🟡: 4A ✅ (#58 `5138725`), 4B ✅ (#59 `1d97612`),
  **4C (Swiss / Nordic / Germany)** remaining.

## Phase 29B.4B — condensed closure evidence
- Promoted the `euronext_regulated_info` scaffold → dedicated `EuronextRegulatedConnector`
  (`apps/api/app/services/sources/connectors/euronext_regulated_info.py`): for a verified
  Euronext issuer — Euronext Paris FR (`MC.PA` LVMH / `RMS.PA` Hermès / `KER.PA` Kering) or
  Amsterdam NL (`ASML.AS` ASML), resolved via `verified_issuer_sources`, **never a fabricated
  filing/headline/date/notice number** — emits ONE bounded **T2 Euronext regulated-information
  venue *reference*** (+ regulator AMF France / AFM Netherlands) at the fixed public URL
  `https://www.euronext.com/en/regulated-information` + honest `primary_filing_unavailable`
  content gap; **network-free at report time**. FR issuers (MC/RMS/KER) carry a
  `requires_translation` (`GapType.translation_required`) marker — honest "not translated this
  phase", not an official-translation claim.
- `company_evidence.py` extended the exchange→regulator map (Euronext Paris PA/FR + Amsterdam
  AS/NL → euronext_regulated_info; added to `REGULATOR_REFERENCE_IDS`) + tightened
  `_relevant_scaffold_ids` (UK still uk_fca_nsm; DE unchanged); `registry.py` moved the scaffold
  → enabled `regulator`/`T2_regulator_or_gov` (registry now **8 enabled / 4 scaffolded**:
  SEDAR+/ASX/Deutsche Börse/Nordic). Non-Euronext → honest `source_not_eligible` gap.
- Tests: backend **2058 pass / 12 skip / 0 fail** (+12 `test_phase29b4b_euronext_disclosures.py`;
  +1 regression fix in `test_phase29b1` `test_26_27`), ruff clean, mypy `71` baseline (no new).
  Frontend N/A. Security: ib-security-agent PASS; pre-PR review APPROVED 10/10.
- Staging VALIDATED (2026-07-27): C — registry/health show euronext_regulated_info=enabled
  regulator/T2, 8 enabled/4 scaffolded, honest content-not-fetched note, secret-free.
  D — MC.PA (LVMH FR) T2 venue reference + honest `primary_filing_unavailable` gap +
  `requires_translation`=true/French + `translation_required` gap; company_ir present.
  E — ASML.AS (NL) T2 reference **without** `requires_translation`; company_ir present.
  F — BA.LSE still uk_fca_nsm, no euronext, BA→BAE no Boeing (4A unaffected). G — AAPL/AMAT
  no euronext item, SEC/company_ir unchanged. H — CFR.SW no euronext, honest not-eligible gap.
  I — schema/safety valid, publication_ready false, human_review true, flags KEPT ON,
  publication admin-gated.
- **Honest scoping note:** connector delta directly observed via authed evidence-preview on all
  6 issuers; report-body/safety/publication fields validated by the untouched-code invariant
  (diff touches only connector+wiring+registry; 29B.4A already proved a regulator reference
  flows into a full council FINAL report via the identical path). Fresh full-council runs
  deliberately skipped to avoid Azure OpenAI TPM burn re-testing unchanged code.

## Phase 29B.4C — plan (not started)
- Continue the promote-scaffold→connector pattern (4A UK, 4B Euronext): promote the
  `deutsche_boerse` and `nordic_disclosures` scaffolds → dedicated connectors.
- Targets: `SAP.DE` SAP (Deutsche Börse / Xetra DE → Bundesanzeiger / BaFin regulated-info
  venue); `PNDORA.CO` Pandora (Nasdaq Copenhagen / Nordic DK). German- and Danish-jurisdiction
  issuers → flagged `requires_translation` (`GapType.translation_required`, pending Phase 30).
- **Honest gap — Swiss / SIX:** the intended Swiss targets `UHR.SW` Swatch and `CFR.SW`
  Richemont trade on SIX Swiss Exchange, for which there is currently **NO scaffold** (scaffold
  set is SEDAR+ / ASX / Deutsche Börse / Nordic). Covering them requires a **new SIX/Swiss
  connector from scratch (not a promotion)** or an honest deferral — do not promise a promotion
  that has no scaffold to promote.
- Same shipped posture as 4A/4B: ONE bounded T2 venue *reference* + honest content gap,
  network-free at report time; live regulator-venue CONTENT fetch stays DEFERRED.

## Decisions
- **Live regulator-venue CONTENT fetch DEFERRED (deliberate, carried forward from 29B.4A/4B).**
  A bounded server-side fetch of the regulator venue reads essentially nothing while adding
  external-regulator fetch surface. The shipped posture is the **honest T2 venue reference +
  explicit `primary_filing_unavailable` content gap** at report time. Live content fetch is a
  future Phase 29B.4 follow-up (applies to 4C too).
- **`requires_translation` is an honesty marker, not a translation.** Non-English-jurisdiction
  issuers (FR in 4B; DE/DK in 4C) carry `GapType.translation_required` = "docs not translated
  in this phase" (pending Phase 30). Never a claim that official English translations exist.
- Scope stays one region per subphase: 4A = UK ✅, 4B = Euronext ✅, 4C = Swiss/Nordic/Germany.
- **Full-council staging re-runs are skipped when a PR's diff is connector/wiring/registry-only**
  (untouched-code invariant) to avoid Azure OpenAI TPM burn — connector delta is proven via
  authed evidence-preview instead.

## Staging flags (unchanged — all ON, KEEP)
`LLM_COUNCIL_ENABLED`=true · `LLM_DISCOVERY_COUNCIL_ENABLED`=true ·
`SOURCE_CONNECTOR_ENABLED`=true · `SOURCE_DOCUMENT_EXTRACTION_ENABLED`=true

## Carry-forward limitations
1. Euronext (and UK FCA NSM) T1 filing CONTENT is not fetched — venue *reference* + honest
   content gap only (deferred; see Decisions).
2. Regulator connectors SEDAR+ / ASX / Deutsche Börse / Nordic remain scaffolded (honest
   gaps) — 4C + later batches. No SIX/Swiss scaffold exists (see 4C honest gap).
3. No local-language translation yet — FR/DE/DK/other non-English primary docs flagged
   `requires_translation` pending Phase 30.
4. (From 29B.3) primary-fact happy path is unit-fixture-proven only; scoring/completeness
   numeric uplift is capability-only (not wired to a production caller).

## Next exact command / action
- **Create branch `feature/phase-29b4c-swiss-nordic-de-disclosures` and scope 29B.4C**
  (Swiss / Nordic / Germany regulated-disclosure connectors). Promote `deutsche_boerse` +
  `nordic_disclosures` scaffolds; decide new-SIX-connector-vs-defer for the Swiss targets.
