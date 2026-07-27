# Session State — Phase 29B.4A CLOSED · next: Phase 29B.4B Euronext (not started) (updated 2026-07-27)

> Resumable snapshot for the current Claude Code session. Overwrite this file at
> each checkpoint (see the `context-compaction` skill). Keep decisions + evidence,
> not raw logs.

## Current position
- On `main`, HEAD `5138725` ("Phase 29B.4A: add bounded UK FCA NSM/RNS regulated-disclosure
  connector (#58)"), clean tree.
- **Phase 29B.4A — CLOSED** (merged + deployed + staging-validated). Closure report:
  `docs/development/closures/phase-29b4a.md`.
- **Next: Phase 29B.4B — Euronext regulated-disclosure connector. Stage: NOT STARTED**
  (no branch / PR yet).
- Umbrella **Phase 29B.4** (EU/UK regulated-disclosure connectors) stays 🟡: 4A ✅ done;
  **4B (Euronext)** next; **4C (Swiss / Nordic / Germany)** after.
- DB head `011` (no migration since discovery/scoring; 29B.4A added none).

## Phase 29B.4A — closed (condensed evidence)
- PR #58 squash-merged to `main`. Merge SHA `5138725`.
- Deploy API — Staging success; API `/health` `commit_sha=5138725` (3 stable polls);
  web unchanged at `793e0a7` (backend-only — no web change). Migration NONE (head `011`).
- Promoted `uk_fca_nsm` scaffold → dedicated `UkFcaNsmConnector`: verified UK-regulated LSE
  issuer (`BRBY.LSE` / `BA.LSE`, resolved via `verified_issuer_sources`, **never Boeing/SEC**)
  → ONE bounded **T2 UK FCA NSM/RNS venue *reference*** (fixed public NSM URL, no fabricated
  filing/headline/date/RNS number) + honest `primary_filing_unavailable` content gap;
  **network-free at report time**. New `regulator_connector_for()` (LSE/GB → uk_fca_nsm) +
  tightened `_relevant_scaffold_ids` (UK → uk_fca_nsm only; DE/FR unchanged); `registry.py`
  moves uk_fca_nsm → enabled `regulator`/`T2_regulator_or_gov` (scaffold set now 5).
- Tests: backend **2046 pass / 12 skip / 0 fail** (+12 `test_phase29b4a_uk_fca_disclosures.py`;
  updated registry/filing-scaffold tests), ruff clean, mypy `71` baseline (no new). Frontend N/A.
- Security: ib-security-agent PASS (network-free, no fabricated filings, plain public HTTPS
  NSM URL, BA→BAE never Boeing, honest gaps); pre-PR review APPROVED (10/10).
- Staging validation **VALIDATED (clean)**: registry/health show uk_fca_nsm=enabled/T2 +
  scaffolded=5 + "content not fetched at report time" note (secret-free); BA.LSE→BAE Systems
  T2 venue reference + honest gap (boeing=0/sec.gov=0/rns#=0/no fabricated filing, schema+safety
  valid, publication_ready false, human_review_required true, 8/8 agents); BRBY.LSE same posture;
  AAPL/AMAT no UK item (unchanged, 8/8); CFR.SW no UK mention, honest not-eligible gap, company_ir
  unchanged; AUTH_TEST_MODE absent, no secret leaks, publication admin-gated.

## Phase 29B.4B — plan (next)
- Apply the **same promote-scaffold→connector pattern proven in 29B.4A** to Euronext:
  verified Euronext-listed issuers **`MC.PA` LVMH, `RMS.PA` Hermès, `KER.PA` Kering,
  `ASML.AS` ASML** (resolved via `verified_issuer_sources`, never a fabricated filing) → ONE
  bounded **T2 regulator-transport venue *reference*** to the Euronext regulated-information
  venue + honest `primary_filing_unavailable` content gap; **network-free at report time**.
- Extend `regulator_connector_for()` (FR/NL/BE/PT → `euronext`), tighten `_relevant_scaffold_ids`,
  and move `euronext` out of the scaffold table in `registry.py` (scaffold set 5 → 4).
- **French-language disclosures flagged `requires_translation`** (French URDs etc.) — pending
  Phase 30, no local-language ingestion this subphase.

## Decisions
- **Live regulator-venue CONTENT fetch DEFERRED (deliberate, carried forward from 29B.4A).**
  The FCA NSM is a JavaScript SPA → a bounded server-side fetch reads essentially nothing while
  adding external-regulator fetch surface. The shipped/planned posture is the **honest T2 venue
  reference + explicit `primary_filing_unavailable` content gap** at report time. Live content
  fetch is a future Phase 29B.4 follow-up (applies to 4B/4C as well).
- Scope stays one region per subphase: 4A = UK (done), 4B = Euronext, 4C = Swiss/Nordic/Germany.

## Staging flags (unchanged — all ON, KEEP)
`LLM_COUNCIL_ENABLED`=true · `LLM_DISCOVERY_COUNCIL_ENABLED`=true ·
`SOURCE_CONNECTOR_ENABLED`=true · `SOURCE_DOCUMENT_EXTRACTION_ENABLED`=true

## Carry-forward limitations
1. UK FCA NSM (and, when built, Euronext) T1 filing CONTENT is not fetched — venue *reference*
   + honest content gap only (deferred; see Decisions).
2. Regulator connectors SEDAR+ / ASX / Euronext / Deutsche Börse / Nordic remain scaffolded
   (honest gaps) — 4B/4C.
3. (From 29B.3) primary-fact happy path is unit-fixture-proven only; scoring/completeness
   numeric uplift is capability-only (not wired to a production caller).

## Next exact command / action
- **Create branch `feature/phase-29b4b-euronext-disclosures` and scope 29B.4B** (Euronext:
  MC.PA / RMS.PA / KER.PA / ASML.AS; same promote-scaffold→connector pattern; French docs
  flagged `requires_translation`). STOP at the merge gate — do not self-merge/close before
  PR review + staging validation.
