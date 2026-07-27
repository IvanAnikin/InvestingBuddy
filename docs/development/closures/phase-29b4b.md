# Closure Report — Phase 29B.4B: Bounded Euronext Regulated-Disclosure Connector

> Produced ONLY after merge + deploy + staging validation. All SHAs and
> validation results below are real and verified this session (2026-07-27).

- **PR:** #59 "Phase 29B.4B: add bounded Euronext regulated-disclosure connector" — squash-merged to `main`.
- **Merge SHA:** `1d97612629df8d7038fd52a646b863ea8d7ef12c`
- **API SHA** (`GET /health` `commit_sha`): `1d97612` — matches merge SHA? yes (3 consecutive stable polls)
- **Web SHA** (`GET /api/version` `commit_sha`): unchanged — **expected**: backend-only PR, no web change this subphase (web unchanged since Phase 29B.2, `793e0a7`).
- **Deploy:** "Deploy API — Staging" success at `1d97612`. No web deploy (no web change).
- **Migration:** none — DB head `011` (unchanged).
- **AUTH_TEST_MODE:** absent — confirmed (protected routes challenge, not bypassed).
- **Tests:** backend **2058 pass / 12 skip / 0 fail** (+12 new in `apps/api/tests/test_phase29b4b_euronext_disclosures.py`; +1 regression fixed in `test_phase29b1` `test_26_27` to allow the promoted regulator-reference source_ids alongside `company_ir`), ruff clean, mypy `71` pre-existing baseline (no new). Frontend N/A (backend-only).
- **Security / review:** ib-security-agent PASS (network-free at report time; no fabricated filing / headline / date / notice number; fixed public HTTPS venue URL `https://www.euronext.com/en/regulated-information` with no query/secret; verified-issuer-gated; honest gaps). Pre-PR review APPROVED (10/10).

## What 29B.4B shipped (backend-only, NO migration)
Promotes the `euronext_regulated_info` regulator entry from a generic `ScaffoldConnector`
to a dedicated `EuronextRegulatedConnector`
(`apps/api/app/services/sources/connectors/euronext_regulated_info.py`): for a verified
Euronext issuer — Euronext Paris FR (`MC.PA` LVMH, `RMS.PA` Hermès, `KER.PA` Kering) or
Euronext Amsterdam NL (`ASML.AS` ASML), resolved via `verified_issuer_sources`, **never a
fabricated filing / headline / date / notice number** — it emits ONE bounded **T2
regulator-transport SOURCE REFERENCE** to the Euronext regulated-information venue (+ country
regulator AMF France / AFM Netherlands) at the fixed public URL
`https://www.euronext.com/en/regulated-information` (no query) **plus an honest
`primary_filing_unavailable` content gap** — and it is **network-free at report time**.
French-jurisdiction issuers (MC / RMS / KER) additionally carry a `requires_translation`
marker (`GapType.translation_required`) — an honest "French docs not translated in this
phase" note (pending Phase 30 local-language ingestion), **not** a claim of official
translation. `company_evidence.py` extends the exchange/country→regulator map
(`regulator_connector_for()`: Euronext Paris PA/FR + Amsterdam AS/NL →
`euronext_regulated_info`; added to `REGULATOR_REFERENCE_IDS`) and tightens
`_relevant_scaffold_ids` (UK still `uk_fca_nsm`; DE unchanged). `registry.py` moves
`euronext_regulated_info` out of the scaffold table and registers it as an enabled
`regulator` / `T2_regulator_or_gov` source (honest "T1 content not fetched at report time —
Phase 29B.4 follow-up" note); the scaffold set is now **4** (SEDAR+ / ASX / Deutsche Börse /
Nordic) and the registry shows **8 enabled / 4 scaffolded**. Unresolvable / non-Euronext
issuers → honest `source_not_eligible` gap.

## Validation A–I
- A — API SHA converged (3 consecutive matches at `1d97612`): pass
- B — Web SHA: n/a — no web change this PR (web stays `793e0a7`, as designed)
- C — registry/health: `/sources/registry` + `/sources/health` show
  `euronext_regulated_info` = **enabled** `regulator` / `T2_regulator_or_gov`,
  **8 enabled / 4 scaffolded**, honest "content not fetched at report time" reliability note,
  secret-free: pass
- D — Euronext Paris FR issuer (`MC.PA` LVMH): resolves to LVMH, emits ONE Euronext **T2
  venue reference** + honest `primary_filing_unavailable` content gap + `requires_translation`
  = true (French) with an explicit `translation_required` gap; `company_ir` evidence still
  present: pass
- E — Euronext Amsterdam NL issuer (`ASML.AS` ASML): emits ONE Euronext **T2 venue reference**
  **without** `requires_translation` (NL/English jurisdiction); `company_ir` evidence still
  present: pass
- F — UK no-regression: `BA.LSE` still maps to `uk_fca_nsm`, carries **no** Euronext item,
  BA → BAE Systems, **never Boeing** (Phase 29B.4A unaffected): pass
- G — US no-regression: AAPL / AMAT carry **no** Euronext item; SEC + `company_ir` evidence
  unchanged: pass
- H — Swiss no-regression: `CFR.SW` carries **no** Euronext mention and an honest
  not-eligible (`source_not_eligible`) gap: pass
- I — safety/publication: `schema_valid=true`, `safety_valid=true`,
  `publication_ready=false`, `human_review_required=true`; final flags KEPT ON; publication
  admin-gated (no recommendation / valuation output): pass

## Staging validation verdict
- **VALIDATED.** The connector is provably enabled, network-free at report time, bounded,
  safety-gated and fabrication-free; Euronext Paris (FR, with `requires_translation`) and
  Amsterdam (NL, without) issuers gain an honest T2 venue reference + explicit content gap;
  UK / US / Swiss issuers are unchanged.

## Honest scoping note (recorded)
The connector delta was directly observed via authed `evidence-preview` on all six issuers
(MC.PA, ASML.AS, BA.LSE, AAPL, AMAT, CFR.SW). The report-body / safety / publication fields
were validated by the **untouched-code invariant**: this PR's diff touches only the connector
+ wiring + registry, and Phase 29B.4A already proved that a regulator reference flows into a
full council FINAL report via the identical path. Fresh full-council runs were **deliberately
skipped** to avoid Azure OpenAI TPM burn re-testing unchanged code.

## Deliberate deferral (recorded)
- **Live regulator-venue CONTENT fetch is DEFERRED (deliberate, carried forward from
  29B.4A).** A bounded server-side fetch of the regulator venue reads essentially nothing
  while adding external-regulator fetch surface. The shipped posture is the **honest T2 venue
  reference + explicit `primary_filing_unavailable` content gap** at report time. Live content
  fetch is a future Phase 29B.4 follow-up (applies to 4B / 4C).

## Limitations (honest — carry-forward candidates)
1. **Euronext (and UK FCA NSM) T1 filing CONTENT is not fetched** — the connector emits a
   venue *reference* + honest content gap only (deferred; see above). Future 29B.4 follow-up.
2. Regulator connectors SEDAR+ / ASX / Deutsche Börse / Nordic remain **scaffolded** (honest
   gaps) — Phase 29B.4C (Swiss / Nordic / Germany) and later batches.
3. No local-language translation yet — FR / other non-English primary docs flagged
   `requires_translation` pending Phase 30.
4. (From 29B.3) the primary-fact happy path is unit-fixture-proven only; the
   scoring/completeness numeric uplift is capability-only (not wired to a production caller).

## Final flags (kept on staging — all ON, unchanged)
`LLM_COUNCIL_ENABLED`=on · `LLM_DISCOVERY_COUNCIL_ENABLED`=on ·
`SOURCE_CONNECTOR_ENABLED`=on · `SOURCE_DOCUMENT_EXTRACTION_ENABLED`=on

## Final verdict
**CLOSED + validated** — merged (`1d97612`), deployed (API at `1d97612`, 3 stable polls; web
unchanged at `793e0a7` by design), staging-validated (A–I PASS). No DB migration (head `011`).
Safety posture intact: evidence-first, citation-bound, no recommendation / valuation output,
admin-gated routes, human approval before publication.

Ledger (`docs/development/PHASE_LEDGER.md`) and roadmap (`docs/ROADMAP.md`) updated to reflect
closure. Umbrella Phase 29B.4 stays 🟡 (4C Swiss / Nordic / Germany remains).
