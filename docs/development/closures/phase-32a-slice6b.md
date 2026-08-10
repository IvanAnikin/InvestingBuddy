# Closure Report — Phase 32A Slice 6B: full-analysis report integrity (C1–C9)

> Produced after merge + deploy + staging validation. All SHAs, IDs and results are real.
> Closed 2026-08-10. Verdict: **Slice 6B CLOSED + STAGING-VALIDATED** (mainline plus two
> corrective hotfixes, each triggered by a real staging failure this validation caught).

## Problem

An E2E QA pass over a real staging full-analysis report for **Burberry Group plc
(BRBY, LSE)** surfaced nine independently-root-caused report-integrity defects. None of
them fabricated financial content, but several presented an inaccurate or internally
contradictory picture of what had actually been sourced — which is exactly the class of
defect this project's evidence-first posture exists to prevent.

## The nine fixes

| | Area | Fix |
|---|---|---|
| C1 | Company identity | Seed/upgrade the `Company` DB row with `DiscoveryCandidate.legal_name` (a real, sourced identity value) in preference to the ticker-like `company_name`, so a report's title/legal_name no longer collapses to the bare ticker. The existing `is_placeholder_company_name` upgrade guard is unchanged — a genuine existing name is never overwritten. |
| C2 | Discovery lineage | A `discovery_lineage` block (discovery_run_id, candidate rank/score, thesis relevance/match) sourced from the run's own `DiscoveryCandidate`/`DiscoveryRun` rows — never inferred from ticker/name matching — threaded into `Report.source_summary_json` and rendered as an additive report section, alongside (not replacing) the legacy `discovery_rationale` section which stays honestly "not available" for `DiscoveryCandidate`-launched reports. |
| C3 | Price-quote currency | Providers no longer hardcode `currency="USD"`; a genuinely unknown quote currency is `None`, rendered `not_sourced` rather than fabricated (CLAUDE.md rule 6). `exchange_registry.price_quote_currency_for_exchange()` distinguishes the price-quote currency (LSE trades in **GBX**/pence) from the issuer's reporting currency (GBP) — never conflated. **Partial at mainline — see Hotfix 2.** |
| C4 | `schema_valid` staleness | `committee_chair_summary.quality_gate_status.schema_valid` refreshed from the same authoritative post-final-assembly validation result already used for `workflow_status`. |
| C5 | Blocking-gap counts | `blocking_gaps_count`/`non_blocking_gaps_count` now read the real gap lists (were reading a key the producer never wrote, silently defaulting to `0`). |
| C6 | Missing-count rename | The financial-agent-scoped `missing_count` renamed `missing_financial_fields_count`, distinct from the whole-report `missing_information` union, and no longer falling back to a misleading `0` when financial data is genuinely absent. |
| C7 | Bot-protection gap message | A bot-protection/challenge-page fetch now produces a distinct, honest gap message from a successful fetch that found zero candidate links. |
| C8 | Stale OCR status text | The two unconditional "no OCR in this phase" literals (stale since Slice 5B.2 shipped real OCR) replaced with text conditioned on the real `primary_document_ocr_enabled` flag and each artifact's actual `failure_code`. |
| C9 | Source/citation scope labeling | The pre-council deterministic-draft `sources`/`citations` envelope now carries an explicit `"scope": "deterministic_pre_council_draft"` marker next to the six broader post-council reconciliation counts, removing an apparent (not actual) contradiction. |

None of these require an Alembic migration — all use existing JSONB columns or pure
Python/display logic.

## PRs / SHAs

| | PR | Squash-merge SHA |
|---|---|---|
| Mainline (C1–C9) | **#90** | `d7c8774` |
| Hotfix 1 — identity (C1) | **#95** | `977cb22` |
| Hotfix 2 — currency (C3) | **#93** | `734fac6` |
| CI-only fix | **#92** | `7f4c985` |

**No Alembic migration** in this slice.

### Hotfix 1 (#95) — identity, found live

`_build_company_identity()` in `final_report_generator.py` **always** preferred
`company_snapshot` over the DB-seeded `company_record`. The snapshot's own `legal_name`
can legitimately **be the ticker** — that is a deliberate anti-fabrication safety stub in
`free_real_provider.py` for exchanges SEC EDGAR does not cover, and it exists because of a
real prior incident where `BA.LSE` became "THE BOEING COMPANY". So the mainline C1 fix
seeded the right value into the DB, and the report generator then ignored it.

Fixed narrowly: prefer the DB record **only** when the snapshot's own name is provably a
placeholder (via the existing `is_placeholder_company_name()` heuristic), never otherwise
— the anti-fabrication stub's protective behaviour is preserved.

Live proof: fresh BRBY report `1b4ff1db-...` (before the fix) showed
`legal_name="BRBY"`; after deploying the fix, a fresh re-run —
report **`7d8be857-6086-40f5-ba64-7f2322c9b352`** — correctly showed
`legal_name="Burberry Group plc"`, `source="company_db_record"`.

### Hotfix 2 (#93) — currency, found live

The mainline C3 fix touched the raw provider classes (`eodhd_provider.py`,
`eodhd_price_only_provider.py`, `stooq_provider.py`) and `build_company_snapshot()` — but
**missed a fourth, separate path** used by the actual production flow
(`provider_name="free_real"`): `FreeRealSnapshot.to_dict()` never threaded currency
through, and `enrich_snapshot_with_free_real()` independently hardcoded
`"currency": "USD"`. Fixed with the same real-value → registry → `not_sourced` pattern.

Live proof: the SAME fresh BRBY report (`7d8be857-...`, post-fix) showed
`latest_close.currency="GBX"` — the correct LSE pence quote unit, distinct from the GBP
reporting currency. No more fabricated USD.

**Documented but deliberately NOT fixed (tracked follow-up):** a related USD default
remains in `market_metrics_enrichment.py`'s derived market-cap fields. It is genuinely
separate scope and is recorded here rather than silently folded in.

### CI-only fix (#92)

Two tests in the Slice 6B currency suite unconditionally required `langchain-openai`,
which is deliberately excluded from CI's dependency set. Fixed by stubbing the import. No
runtime behaviour change.

## Staging validation (live, real data)

Primary evidence report: **`7d8be857-6086-40f5-ba64-7f2322c9b352`** (BRBY, post-hotfix),
plus the pre-hotfix report `1b4ff1db-...` used as the before/after identity contrast.

- **C1 identity** — `legal_name="Burberry Group plc"`, `source="company_db_record"`
  (after Hotfix 1; `"BRBY"` before it).
- **C3 currency** — `latest_close.currency="GBX"` (after Hotfix 2).
- **C2 discovery lineage** — fully populated with real run/candidate/rank/thesis data.
- **C4 schema_valid consistency** — `schema_valid=true` consistent in **both**
  `workflow_status` and `committee_chair_summary.quality_gate_status`.
- **C5 blocking gaps** — `blocking_gaps_count=32`, matching the narrative.
- **C6 missing counts** — `missing_financial_fields_count` and the whole-report
  `total_missing_items` correctly distinct, with the `scope_note` present.
- **C7 bot-protection message** — Burberry's IR bot protection correctly surfaced as
  "Company IR source fetch was blocked due to bot protection…", not the old generic
  zero-links message.
- **C8 OCR text** — correctly reads "no document candidate discovered — OCR was not
  reached"; the stale "no OCR in this phase" string is gone.
- **C9 scope labeling** — source/citation appendix correctly scope-labelled
  `"deterministic_pre_council_draft"` alongside the six broader reconciliation counts.

C2, C4, C5, C6, C7, C8 and C9 were **all confirmed correct on the first live pass** — no
further fixes were needed for those seven. Only C1 and C3 required the corrective
hotfixes above.

### Deployment facts

- API deployed to staging (`ib-stg-api`) at `b2aa1be`
  (`b2aa1bebcf3ec724b61b6477ce54770f861fdd2c`), verified via 5 consecutive `/health`
  checks matching exactly. Web deployed at `dee5998` (unaffected by the backend-only
  hotfixes).
- No new flags introduced by this slice; `AUTH_TEST_MODE` confirmed absent.
- Security spot-checks: unauthenticated `GET /reports/{id}` → 401.

## Honest limitations / carry-forward

- **Not fixed, tracked:** the derived market-cap USD default in
  `market_metrics_enrichment.py` (Hotfix 2 above). Separate scope, deliberately deferred,
  recorded rather than hidden.
- Validation was performed against **one real issuer** (BRBY/LSE) — the issuer whose
  report produced the nine findings. Other exchanges/quote conventions were not
  re-validated live in this pass beyond what the offline suite covers.
- C1's fix is narrow by design: it only prefers the DB record when the snapshot name is a
  provable placeholder. An issuer whose snapshot carries a **wrong but non-placeholder**
  name would still win over the DB record — that is intentional (the anti-fabrication
  stub must not be overridden by guesswork), not an oversight.

## Verdict

**Slice 6B CLOSED + STAGING-VALIDATED, 2026-08-10.** Mainline #90 → `d7c8774`, plus
corrective hotfixes #95 → `977cb22` (identity) and #93 → `734fac6` (currency), plus the
CI-only #92 → `7f4c985`. All nine findings are confirmed corrected on a real, fresh
staging BRBY report, with the two hotfixes proven by direct before/after evidence on live
reports rather than by inspection alone.
