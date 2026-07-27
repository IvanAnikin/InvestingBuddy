# Closure Report — Phase 29B.4A: Bounded UK FCA NSM/RNS Regulated-Disclosure Connector

> Produced ONLY after merge + deploy + staging validation. All SHAs and
> validation results below are real and verified this session (2026-07-27).

- **PR:** #58 "Phase 29B.4A: add bounded UK FCA NSM/RNS regulated-disclosure connector" — squash-merged to `main`.
- **Merge SHA:** `5138725b77df478beded2701293df6a3ea8f7cc3`
- **API SHA** (`GET /health` `commit_sha`): `5138725` — matches merge SHA? yes (3 consecutive stable polls)
- **Web SHA** (`GET /api/version` `commit_sha`): `793e0a7` — matches merge SHA? no — **expected**: backend-only PR, no web change this subphase (web unchanged since Phase 29B.2).
- **Deploy:** "Deploy API — Staging" success at `5138725`. No web deploy (no web change).
- **Migration:** none — DB head `011` (unchanged).
- **AUTH_TEST_MODE:** absent — confirmed (protected routes challenge, not bypassed).
- **Tests:** backend **2046 pass / 12 skip / 0 fail** (+12 new in `apps/api/tests/test_phase29b4a_uk_fca_disclosures.py`; updated `test_phase29a_source_registry.py` / `test_phase29b_filing_connectors.py` scaffold tests for the promotion), ruff clean, mypy `71` pre-existing baseline (no new). Frontend N/A (backend-only).
- **Security / review:** ib-security-agent PASS (network-free at report time; no fabricated filing / headline / date / RNS number; `FCA_NSM_URL` is a plain public HTTPS venue link with no query/secret; `BA.LSE` → BAE Systems, **never Boeing**; honest gaps). Pre-PR review APPROVED (10/10).

## What 29B.4A shipped (backend-only, NO migration)
Promotes the `uk_fca_nsm` regulator entry from a generic `ScaffoldConnector` to a
dedicated `UkFcaNsmConnector` (`apps/api/app/services/sources/connectors/uk_fca_nsm.py`):
for a verified UK-regulated LSE issuer (`BRBY.LSE` Burberry, `BA.LSE` BAE Systems —
resolved via `verified_issuer_sources`, never Boeing / SEC) it emits ONE bounded
**T2 regulator-transport SOURCE REFERENCE** citing the issuer's FCA National Storage
Mechanism / RNS venue (fixed public NSM URL, no query, no fabricated filing/headline/
date/RNS number) **plus an honest `primary_filing_unavailable` content gap** — and it is
**network-free at report time**. A new `regulator_connector_for()` exchange/country→
regulator map (LSE/GB → `uk_fca_nsm`) plus tightened `_relevant_scaffold_ids` map a UK/LSE
issuer to `uk_fca_nsm` only (DE/FR unchanged). `registry.py` moves `uk_fca_nsm` out of the
scaffold table and registers it as an enabled `regulator` / `T2_regulator_or_gov` source
(honest reliability note "T1 content not fetched at report time — Phase 29B.4 follow-up");
the scaffold set is now **5** (SEDAR+ / ASX / Euronext / Deutsche Börse / Nordic).

## Validation A–I
- A — API SHA converged (3 consecutive matches at `5138725`): pass
- B — Web SHA matches: n/a — no web change this PR (web stays `793e0a7`, as designed)
- C — migration state as expected (head `011`, no migration): pass
- D — AUTH_TEST_MODE absent: pass
- E — registry/health: `/sources/registry` + `/sources/health` show `uk_fca_nsm` =
  **enabled** `regulator` / `T2_regulator_or_gov`, `scaffolded` set = **5**, honest
  "T1 content not fetched at report time" reliability note, secret-free: pass
- F — UK issuer report (`BA.LSE`, `BRBY.LSE`): resolves to BAE Systems / Burberry, emits
  ONE FCA NSM **T2 venue reference** + honest `primary_filing_unavailable` gap; `company_ir`
  evidence still present; `boeing`=0, `sec.gov`=0, RNS number=0, no fabricated filing;
  `schema_valid=true`, `safety_valid=true`, `publication_ready=false`,
  `human_review_required=true`, 8/8 council agents: pass
- G — non-UK no-regression: AAPL / AMAT carry **no** UK item and are unchanged (8/8 agents);
  CFR.SW carries **no** UK mention, an honest not-eligible gap, and unchanged `company_ir`
  evidence; final flags KEPT ON (behavior-confirmed): pass
- H — logs / no-secrets: pass (no secret leaks; AUTH_TEST_MODE absent)
- I — safety/publication (no recommendation/valuation; `/admin/*` OAuth-gated; human review
  required; `publication_ready=false`): pass

## Staging validation verdict
- **VALIDATED (clean).** The connector is provably enabled, network-free at report time,
  bounded, safety-gated and fabrication-free; UK issuers gain an honest T2 venue reference +
  explicit content gap; non-UK issuers are unchanged.

## Deliberate deferral (recorded)
- **Live FCA-NSM content fetch is DEFERRED (deliberate).** The NSM is a JavaScript SPA, so a
  bounded server-side fetch reads essentially nothing while adding external-regulator fetch
  surface. The shipped posture is the **honest T2 venue reference + explicit
  `primary_filing_unavailable` content gap** at report time. Live content fetch is a future
  Phase 29B.4 follow-up.

## Limitations (honest — carry-forward candidates)
1. **UK FCA NSM T1 filing CONTENT is not fetched** — the connector emits a venue *reference*
   + honest content gap only (deferred; see above). Future 29B.4 follow-up.
2. Regulator connectors SEDAR+ / ASX / Euronext / Deutsche Börse / Nordic remain
   **scaffolded** (honest gaps) — Phase 29B.4B (Euronext) and 4C (Swiss / Nordic / Germany).
3. (From 29B.3) the primary-fact happy path is unit-fixture-proven only; the
   scoring/completeness numeric uplift is capability-only (not wired to a production caller).

## Final flags (kept on staging — all ON, unchanged)
`LLM_COUNCIL_ENABLED`=on · `LLM_DISCOVERY_COUNCIL_ENABLED`=on ·
`SOURCE_CONNECTOR_ENABLED`=on · `SOURCE_DOCUMENT_EXTRACTION_ENABLED`=on

## Final verdict
**CLOSED + validated** — merged (`5138725`), deployed (API at `5138725`, 3 stable polls;
web unchanged at `793e0a7` by design), staging-validated (A–I PASS, clean). No DB migration
(head `011`). Safety posture intact: evidence-first, citation-bound, no recommendation /
valuation output, admin-gated routes, human approval before publication.

Ledger (`docs/development/PHASE_LEDGER.md`) and roadmap (`docs/ROADMAP.md`) updated to
reflect closure. Umbrella Phase 29B.4 stays 🟡 (4B Euronext + 4C Swiss/Nordic/Germany remain).
