# Staging Validation Plan — Phase 32A · Slice 2: evidence-pack financial-fact wiring + category budgets

> Drafted at the PR gate (part of the implementation report). Execute it AFTER
> the human-approved merge/deploy. Runs read-only against staging; never changes
> app settings or deploys. **This slice adds ONE new default-OFF flag
> (`llm_council_evidence_budgets_enabled`) and NO migration** — so the ON-state
> acceptance criteria require a human-approved app-setting flip (the session
> cannot change app settings). Validation is otherwise pure observation.
>
> Status at draft time: **PR-ready — NOT merged, NOT deployed, NOT
> staging-validated.** Do not fill the closure report until the merge SHA and the
> A–I outcomes below are real.

## Endpoints
- API: `https://ib-stg-api.azurewebsites.net` — health `GET /health`
- Web: `https://ib-stg-web.azurewebsites.net` — version `GET /api/version`
- Admin `/admin` + report/regeneration routes are behind GitHub OAuth → validate
  via SHA + API data (authed `az`/`STAGING_BASIC_AUTH` where scoped), not a live
  browser walk.

## What this slice changed (backend-only)
Wires the already-collected structured SEC/XBRL financials into the LLM council
evidence pack as **tier-split** items (income / cash-flow / balance-sheet facts at
**T1 content / T2 EDGAR transport**; derived margins/ROE/FCF/YoY at **T6 DERIVED**;
market cap/EV/P-E at **T6**; latest close / 52-week at **T5**) and adds a
**category-aware budgeter** with a guaranteed `financial_fact` floor, per-category
caps (`price_trend_cap` / aggregate `news_cap` / strict `low_tier_news_cap`),
near-duplicate news dedup, materiality-aware ranking, and a conservative
derivative-instrument (e.g. AAPD/AAPU) demotion. All gated by the new default-OFF
flag `llm_council_evidence_budgets_enabled`; flag OFF ⇒ evidence pack + budgeter
byte-identical to Phase 29B.2. Web is unchanged. No migration.

## Observability caveat (read first)
The evidence pack is **council INPUT**, not a report field, and (by security
design) its contents are **never logged**. So the ON-state acceptance criteria are
observed **indirectly** via:
- the council agents' OUTPUT in the persisted report (`source_summary_json.llm_council`
  — Financial Analyst / Valuation Guard key points + `evidence_item_count`), and
- the `evidence_pack_built` log event (**counts only**: `evidence_item_count`,
  `known_gap_count`).
The pass signal is that the **Financial Analyst no longer reports "financial data
unavailable"** and its key points cite the revenue / income / margin / cash-flow /
balance-sheet / debt evidence, while Valuation Guard receives the inputs yet still
withholds an unsupported valuation. There is **no raw-pack endpoint** in this slice
(none is added — that would be a logging/security change out of scope). Note this
indirection honestly in the closure.

## Checks (map to closure report A–I)

- **A — API SHA:** `curl -s .../health | grep commit_sha` == merge SHA; poll until
  3 consecutive matches (stale-worker window ~40s + the flag-flip restart later).
- **B — Web SHA:** **No web files changed** → the web SHA is expected to STAY at its
  last-deployed value, NOT advance to the Slice-2 merge SHA (unless a deploy-both
  instruction redeploys web). An unchanged web SHA is NOT a failure.
- **C — Migration:** DB head remains **`011`** — this slice adds NONE. Confirm head
  unchanged (`git show --stat <merge SHA>` ships no alembic file; `alembic current`
  human-run if queryable, else note it is inferred).
- **D — AUTH_TEST_MODE:** absent — an unauth call to a protected route (e.g. POST
  `/api/v1/final-reports/from-company` or the run route) returns an auth challenge,
  not a bypass; `/health` environment = staging.

- **E — OFF-state dark check (BEFORE the flip): no regression.** With
  `llm_council_evidence_budgets_enabled` absent/OFF, generate (or regenerate) an
  AAPL/US/free_real/use_llm report and assert:
  - the report is consistent with pre-Slice-2 behaviour (no new item types forced
    into the council path; budgeter runs its flat Phase-29B.2 path);
  - `schema_valid`/`safety_valid`/`human_review_required` = true,
    `publication_ready` = false;
  - Slice-1 invariants intact: Apple identity, `is_mock`=false,
    `data_provenance`=real.

- **F — ON-state (human-approved flip `llm_council_evidence_budgets_enabled=true`):
  the acceptance criterion.** After the human flips the app setting and the app
  restarts (wait out the dip; 3 stable SHA polls), generate a **NEW**
  AAPL/US/free_real/use_llm report (via the admin run-analysis / from-company
  path). Assert on the persisted report's council metadata + agent outputs:
  1. **Financial evidence is present in the council analysis** — Financial Analyst
     acknowledges available **revenue, net income, gross/operating margins,
     operating & free cash flow, balance-sheet (assets/liabilities/equity/cash) and
     debt** evidence (it must NOT say detailed financial data was unavailable).
  2. **Valuation Guard receives the financial inputs** yet still withholds an
     unsupported valuation / price target / fair value (no such conclusion appears).
  3. **Financial evidence survives ≥20 news events** — even on a news-heavy AAPL
     run the financial-fact floor holds (the T1/T2 SEC statement facts are not
     crowded out); `evidence_item_count` is bounded (≤20 on the budgeted path).
  4. **Primary/regulator evidence outranks aggregator news**; news duplicates and
     derivative-instrument noise (AAPD/AAPU) are reduced.
  5. **Provenance is honest:** SEC/XBRL facts labelled T1/T2; price T5; derived
     metrics + market cap/EV/P-E labelled **DERIVED / T6**, never T1/T2; **annual
     SEC values never presented as TTM**; **no fabricated EBITDA, EV/EBITDA, beta or
     TTM** values anywhere.
  6. `data_provenance`=real, `is_mock`=false, Apple identity preserved (Slice 1);
     `schema_valid`=true, `safety_valid`=true, `human_review_required`=true,
     `publication_ready`=false.
  - **Expected bounded omission (by design — not a failure):** under the on-path
    budget the T1/T2 statement facts are floor-protected and **margins + market
    metrics + price survive**; lower-priority context (**trend signals /
    `financial_data_summary`**) may be **honestly omitted** to fit
    `price_trend_cap` (recorded in `omitted_reason`). "bounded price/trend evidence"
    is the acceptance wording — a dropped trend item is expected, not a defect. (If
    a future decision wants all T5/T6 metric families to always survive, bump
    `llm_council_evidence_price_trend_cap` — a config-only follow-up.)

- **G — CFR contrast (metadata-only stays honest):** with the flag ON, generate /
  regenerate a CFR.SW (Richemont) report and assert its metadata-only primary-source
  references + extraction gaps remain honest and are **NOT** converted into financial
  facts (no `sec_financial_statement` items appear for a company with only
  `metadata_only`/`link_metadata_only` references); the Phase-31 hotfix
  `_source_reference_summary` counts are unchanged. No fabricated financials.

- **H — Logs / no-secrets:** tail recent app logs (human-run scoped `az` if needed),
  `grep -a` for token/secret patterns → none leak. Confirm the evidence path logs
  **counts/labels only** (`evidence_pack_built` = counts; `omitted_reason` = a
  caps/counts sentence) — never pack contents / excerpts / snapshot / URLs. If the
  current-build log tail is not read-only accessible, fall back to scanning the API
  response surface (must be secret-free) + note the mitigation (this slice adds no
  new network and no raw-text logging).

- **I — Safety / publication:** no recommendation / valuation / price-target
  language in any output (the safety gate is unchanged; the pack is council input,
  not scanned product output, but the council OUTPUT must stay clean);
  `publication_ready` stays false and publication stays admin-gated, not public;
  `human_review_required` stays true.

## Gotchas (from prior phases + this slice)
- **The ON-state needs the human flip.** The session cannot change app settings.
  The OFF-state dark check (E) is observable without a flip; the acceptance criteria
  (F) require `llm_council_evidence_budgets_enabled=true`. An app-setting change →
  async restart; a poll can hit an old worker (~40s) — wait out the restart before
  the F run.
- **The budgeter only runs when `source_connector_enabled` is ON** (already ON on
  staging). Category budgeting is additionally gated by the new flag. Both must be
  ON to exercise the category path.
- **Council partial-agent failures on staging are usually Azure gpt-4.1-mini TPM**
  (environmental, not a Slice-2 defect; Slice 4 hardens reliability). If Financial
  Analyst / Valuation Guard are among the agents that DID run, the F assertions hold
  on their output; if TPM skips one of them, note it environmentally and rely on
  `evidence_item_count` + the other agents' citations of the financial evidence.
- **Evidence pack is not a report field** — observe via council agent outputs +
  counts (see the observability caveat). Do not expect a raw pack dump.
- run-analysis is synchronous; `latest_report` can transiently show a legacy draft —
  read the specific report id, not `latest_report`, when asserting F.
- Web SHA should NOT advance (backend-only) — do not treat an unchanged web SHA as a
  failure.
- Azure log tail can be binary → `grep -a`; may be archive-lag affected → note as
  environmental if the current-build tail is unavailable.

## Rollback
Flip `llm_council_evidence_budgets_enabled` back to OFF (or revert the PR); the
envelope/pack path returns to the byte-identical Phase-29B.2 behaviour. No data
migration to unwind.

## Result
Fill `docs/development/templates/closure_report.md` from the A–I outcomes above.
Do NOT print a CLOSED verdict, and do NOT mark this slice ✅ in
`docs/development/PHASE_LEDGER.md` / `docs/ROADMAP.md`, until the merge SHA,
converged API SHA, the OFF-state dark check, the ON-state (post-flip) AAPL
acceptance criteria, the CFR contrast, and the log-scan results are real.
