# Session State — Phase 29B.4A UK FCA NSM/RNS Connector · stage: PR (about to open) (updated 2026-07-27)

> Resumable snapshot for the current Claude Code session. Overwrite this file at
> each checkpoint (see the `context-compaction` skill). Keep decisions + evidence,
> not raw logs.

## Current position
- On branch `feature/phase-29b4a-uk-fca-rns-disclosures`, HEAD `f80511c`
  ("Phase 29B.4A: promote uk_fca_nsm to a dedicated UK regulated-disclosure connector"),
  clean tree.
- **Phase 29B.4A — stage: PR (about to open).** Not yet merged / deployed / staging-validated.
- Umbrella **Phase 29B.4** (EU/UK regulated-disclosure connectors): 4A UK FCA NSM in
  progress; **4B (Euronext)** and **4C (Swiss / Nordic / Germany)** still upcoming.
- Prior phase **29B.3 — CLOSED** (merged + staging-validated at `29f4a84`; closure report
  `docs/development/closures/phase-29b3.md`).
- DB head `011` (no migration since discovery/scoring; 29B.4A adds none).

## What 29B.4A changed (backend-only, 7 files, NO migration)
- New `apps/api/app/services/sources/connectors/uk_fca_nsm.py` — `UkFcaNsmConnector`:
  for a verified UK-regulated LSE issuer (`BRBY.LSE` Burberry, `BA.LSE` BAE Systems —
  resolved via `verified_issuer_sources`, **never Boeing / SEC**) emits ONE bounded
  **T2 regulator-transport SOURCE REFERENCE** to the issuer's FCA National Storage
  Mechanism / RNS venue (fixed public NSM URL `https://data.fca.org.uk/#/nsm/nationalstoragemechanism`
  — no query, **no fabricated filing / headline / date / RNS number**) **plus an honest
  `primary_filing_unavailable` gap** that the T1 filing CONTENT is not fetched at report
  time. **Network-free at report time.** Non-UK / non-verified → honest gap.
- `company_evidence.py` — new `regulator_connector_for()` exchange/country→regulator
  mapping (LSE/GB → `uk_fca_nsm`) + tightened `_relevant_scaffold_ids` so a UK/LSE issuer
  maps to `uk_fca_nsm` **only** (not all four Europe scaffolds; DE/FR unchanged).
- `registry.py` — `uk_fca_nsm` removed from the scaffold table, registered as an
  **enabled `regulator` / `T2_regulator_or_gov`** source (honest reliability note
  "T1 content not fetched at report time — Phase 29B.4 follow-up"). `/sources/registry`
  + `/sources/health` now show it enabled (was `scaffolded`); scaffold set now **5**
  (SEDAR+/ASX/Euronext/Deutsche Börse/Nordic). Registry `summary`: `enabled: 7` /
  `scaffolded: 5` (total 31 unchanged).
- `connectors/__init__.py` — export `UkFcaNsmConnector`.
- Tests: `tests/test_phase29b4a_uk_fca_disclosures.py` (new) + updates to
  `test_phase29a_source_registry.py` / `test_phase29b_filing_connectors.py`.
- **No new config flag, no new allowlisted host, no migration.**

## Decisions
- **Live FCA-NSM content fetch DEFERRED (deliberate).** The NSM is a JavaScript SPA, so a
  bounded server-side fetch reads essentially nothing (→ mostly honest gaps) while adding
  external-regulator fetch surface. The chosen posture is the **honest T2 venue reference +
  explicit `primary_filing_unavailable` content-gap** at report time. Live content fetch is
  a future 29B.4 follow-up. This still satisfies all 8 phase acceptance criteria
  (allowlisted/bounded, no arbitrary fetch, no fake filings, BA≠Boeing, company IR intact,
  CFR unchanged, schema/safety valid, explicit gaps).
- Scope kept to UK (4A). Euronext (4B) and Swiss/Nordic/Germany (4C) are later subphases.

## Verification (GREEN)
- Backend **2046 pass / 12 skip / 0 fail**, ruff clean, mypy `71` baseline (no new).
- Security scan **PASS**.
- No BUY/SELL/HOLD/WATCH/target/fair-value/upside/recommendation; `human_review_required=true`,
  `publication_ready=false`, no publish route, no auth change.

## Staging flags (unchanged — all ON, KEEP)
`LLM_COUNCIL_ENABLED`=true · `LLM_DISCOVERY_COUNCIL_ENABLED`=true ·
`SOURCE_CONNECTOR_ENABLED`=true · `SOURCE_DOCUMENT_EXTRACTION_ENABLED`=true

## Carry-forward limitations
1. **UK FCA NSM T1 filing CONTENT is not fetched** — the connector emits a venue *reference*
   + honest content gap only (deferred; see Decisions). Future 29B.4 follow-up.
2. Regulator connectors SEDAR+ / ASX / Euronext / Deutsche Börse / Nordic remain scaffolded
   (honest gaps) — 4B/4C.
3. (From 29B.3) primary-fact happy path is unit-fixture-proven only; scoring/completeness
   numeric uplift is capability-only (not wired to a production caller).

## Next exact command / action
- **Run `ib-pr-review-agent`, then `gh pr create` for 29B.4A; STOP at the merge gate**
  (do not self-merge/close before PR review + staging validation).
