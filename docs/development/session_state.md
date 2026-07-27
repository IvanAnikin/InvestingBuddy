# Session State — Phase 29B.4B Euronext · stage: PR (about to open) (updated 2026-07-27)

> Resumable snapshot for the current Claude Code session. Overwrite this file at
> each checkpoint (see the `context-compaction` skill). Keep decisions + evidence,
> not raw logs.

## Current position
- On `feature/phase-29b4b-euronext-disclosures`, HEAD `cd6b02a` ("test: allow
  promoted regulator-reference source_ids in 29B.1 non-US honesty test"), clean tree.
  Branched from `main` @ `5138725` (Phase 29B.4A).
- **Phase 29B.4B — Euronext regulated-disclosure connector. Stage: PR (about to open).**
  Backend-only, verified GREEN, docs synced. **Not yet merged / deployed / staging-validated.**
- Umbrella **Phase 29B.4** (EU/UK regulated-disclosure connectors) stays 🟡: 4A ✅ done
  (#58 `5138725`, staging-validated); **4B (Euronext)** in progress (this branch);
  **4C (Swiss / Nordic / Germany)** after.
- DB head `011` (no migration — 29B.4B adds none).

## Phase 29B.4B — condensed evidence (this branch)
- Promotes the `euronext_regulated_info` scaffold → dedicated `EuronextRegulatedConnector`
  (`apps/api/app/services/sources/connectors/euronext_regulated_info.py`): for a verified
  Euronext issuer — Euronext Paris FR (`MC.PA` LVMH / `RMS.PA` Hermès / `KER.PA` Kering) or
  Euronext Amsterdam NL (`ASML.AS` ASML), resolved via `verified_issuer_sources`, **never a
  fabricated filing/headline/date/notice number** — it emits ONE bounded **T2 Euronext
  regulated-information venue *reference*** (+ country regulator AMF France / AFM Netherlands)
  at the fixed public URL `https://www.euronext.com/en/regulated-information` (no query) +
  honest `primary_filing_unavailable` content gap; **network-free at report time**.
- French-jurisdiction issuers (MC/RMS/KER) carry a `requires_translation` flag
  (`GapType.translation_required`) — honest "French docs not translated in this phase",
  **not** a claim of official translation.
- `company_evidence.py` extends the exchange/country→regulator map (Euronext Paris PA/FR +
  Amsterdam AS/NL → `euronext_regulated_info`; added to `REGULATOR_REFERENCE_IDS`) + tightens
  `_relevant_scaffold_ids` (FR/NL Euronext → euronext_regulated_info only; UK still uk_fca_nsm;
  DE unchanged). `registry.py` moves `euronext_regulated_info` scaffold → **enabled
  `regulator`/`T2_regulator_or_gov`** (registry now **8 enabled / 4 scaffolded**:
  SEDAR+/ASX/Deutsche Börse/Nordic). Unresolvable / non-Euronext → honest `source_not_eligible` gap.
- 9 files (incl. tests), NO migration. One adjacent test fix: `test_phase29b1` test_26_27 now
  allows `REGULATOR_REFERENCE_IDS` alongside company_ir (honesty checks intact).
- Tests: backend **2058 pass / 12 skip / 0 fail** (+`test_phase29b4b_euronext_disclosures.py`),
  ruff clean, mypy `71` baseline (no new). Frontend N/A.
- Security: ib-security-agent **PASS** (network-free, no fabricated filings, plain public HTTPS
  venue URL, verified-issuer-gated, honest gaps).
- Docs synced this checkpoint: ARCHITECTURE.md, API.md, ROADMAP.md, PHASE_LEDGER.md, this file,
  `.env.example` (scaffold-comment now SEDAR+/ASX/Deutsche Börse/Nordic).

## Decisions
- **`requires_translation` is an honesty marker, not a translation.** FR issuers (MC/RMS/KER)
  carry `GapType.translation_required` = "French docs not translated in this phase" (pending
  Phase 30 local-language ingestion). Never a claim that official English translations exist.
- **Live regulator-venue CONTENT fetch DEFERRED (deliberate, carried forward from 29B.4A).**
  A bounded server-side fetch of the regulator venue reads essentially nothing while adding
  external-regulator fetch surface. The shipped posture is the **honest T2 venue reference +
  explicit `primary_filing_unavailable` content gap** at report time. Live content fetch is a
  future Phase 29B.4 follow-up (applies to 4B/4C).
- Scope stays one region per subphase: 4A = UK (done), 4B = Euronext (this branch),
  4C = Swiss/Nordic/Germany (`UHR.SW` / `CFR.SW` / `PNDORA.CO` / `SAP.DE`).

## Staging flags (unchanged — all ON, KEEP)
`LLM_COUNCIL_ENABLED`=true · `LLM_DISCOVERY_COUNCIL_ENABLED`=true ·
`SOURCE_CONNECTOR_ENABLED`=true · `SOURCE_DOCUMENT_EXTRACTION_ENABLED`=true

## Carry-forward limitations
1. Euronext (and UK FCA NSM) T1 filing CONTENT is not fetched — venue *reference* + honest
   content gap only (deferred; see Decisions).
2. Regulator connectors SEDAR+ / ASX / Deutsche Börse / Nordic remain scaffolded (honest
   gaps) — 4C + later batches.
3. No local-language translation yet — FR/other non-English primary docs flagged
   `requires_translation` pending Phase 30.
4. (From 29B.3) primary-fact happy path is unit-fixture-proven only; scoring/completeness
   numeric uplift is capability-only (not wired to a production caller).

## Next exact command / action
- **Run ib-pr-review-agent, then `gh pr create` for 29B.4B** (branch
  `feature/phase-29b4b-euronext-disclosures`). **STOP at the merge gate** — do not
  self-merge/close before PR review + human approval + staging validation.
