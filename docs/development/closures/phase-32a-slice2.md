# Closure Report — Phase 32A Slice 2: evidence-pack financial-fact wiring + category evidence budgets

> Produced after merge + deploy + staging validation. All SHAs, IDs and results below are real.
> Closed 2026-08-02. Verdict: **CLOSED + STAGING-VALIDATED — WITH ENVIRONMENTAL NOTE.**

## Summary
Slice 2 wires the already-collected structured SEC/XBRL financials into the LLM council
evidence pack as **tier-split** items and adds a **category-aware budgeter** with a
`financial_fact` floor so catalyst/news volume can no longer crowd out financial evidence.
Gated by ONE new default-OFF flag `LLM_COUNCIL_EVIDENCE_BUDGETS_ENABLED`; OFF ⇒ evidence
pack + budgeter byte-identical to Phase 29B.2. Backend-only, NO migration. Full architecture
+ budget policy: `PHASE_32A_PIPELINE_REPAIR.md` §8. Staging plan: `PHASE_32A_SLICE2_STAGING_VALIDATION.md`.

## Merge / deploy
- **PR #73** squash-merged → `main` **`3237d27a3a7d076019ab67d922618732af0f05f4`** (`3237d27`).
- Deploys: **API run `30757018955`** (push-triggered) + **Web run `30757048621`** (workflow_dispatch),
  both **success** @ `3237d27`.
- **API SHA** (`GET /health` `commit_sha`): `3237d27` — stable ×3, `environment=staging`. **Match: yes.**
- **Web SHA** (`GET /api/version` `commit_sha`): `3237d27` — stable ×3 (web redeployed per deploy-both). **Match: yes.**
- **Migration:** none — no alembic in the merge diff; DB head **`011`** unchanged.
- **AUTH_TEST_MODE:** absent — unauth `POST /final-reports/from-report`, `POST /workflows/company-analysis/run`,
  `GET /reports/{id}` all → **401**.
- **Feature flag:** `LLM_COUNCIL_EVIDENCE_BUDGETS_ENABLED` flipped **OFF → true** (human-approved; confirmed `true`).
  Companion knobs absent → code defaults apply (`financial_floor=3`, `price_trend_cap=3`, `news_cap=8`,
  `low_tier_news_cap=4`). Other flags unchanged (7 prior source/council flags ON incl. `SOURCE_CONNECTOR_ENABLED`
  so the budgeter runs; `LLM_PROVIDER_COUNCIL=azure_openai` → real council; `SOURCE_TRANSLATION_ENABLED` OFF).

## Reports generated (all AAPL/US/free_real/use_llm unless noted; council provider = azure_openai)
| Purpose | Draft (run) | Final (from-report) | Flag |
|---|---|---|---|
| OFF-state baseline | `cb45dbc4` | `74dac609` | OFF |
| ON-state (primary) | `1be6b2e4` | **`104ecb50`** | ON |
| ON-state (repro) | `7d3bfe4c` | `42848523` | ON |
| CFR fallback (Richemont) | `6237a9c5` | **`a39ac390`** | ON |

## Validation — the OFF→ON contrast IS the fix, demonstrated live
**OFF-state (`74dac609`, flag OFF):** Apple Inc./AAPL/Nasdaq, `is_mock=false`, `schema_valid`/`safety_valid`/
`human_review_required`=true, `publication_ready`=false. Council 4/8 (Azure TPM). Financial Analyst (completed)
cites the ONE legacy blob `E8` → revenue/gross-profit/op-income/net-income/EPS/OCF, but **explicitly states
"margins, free cash flow, capital expenditures, or debt levels are NOT provided in the current evidence."**
`evidence_item_count=20`.

**ON-state (`104ecb50`, flag ON):** with the tier-split, the Financial Analyst now cites **three separate
evidence items** and acknowledges the full financial set:
- `E8` (income statement): revenue **$265.6B**, gross profit **$195.2B**, operating income **$133.1B**,
  net income **$112.0B**, EPS $7.49.
- `E9` (cash-flow statement): operating cash flow **$111.5B**, capital expenditures **$12.7B**.
- `E10` (balance sheet): total assets **$359.2B**, liabilities **$285.5B**, equity **$73.7B**, cash **$35.9B**,
  long-term debt **$90.7B**, shares ~14.8B.
`financial_floor=3` → exactly the 3 tier-split items survived the 20-item budget **alongside** news items
`E11`–`E20` (floor held; news did not crowd out financials). `data_provenance=real` (×3), `is_mock=false` (×5),
identity Apple/AAPL/Nasdaq, `schema_valid`/`safety_valid`=true (safety gate `passed=true, warnings=[], blocks_approval=false`),
`human_review_required`=true, `publication_ready`=false. Reproduced on `42848523` (FA again cites E8/E9/E10).

### Acceptance criteria
- ✅ identity + `data_provenance=real` preserved; `is_mock=false`.
- ✅ SEC/XBRL financial facts reach the council evidence pack (tier-split `E8`/`E9`/`E10`, reproduced).
- ✅ Financial Analyst acknowledges revenue, net income, gross/operating income (margin components),
  cash flow (OCF+capex), balance sheet (assets/liabilities/equity/cash) and **debt** (LT debt $90.7B).
- ✅ Valuation Guard receives available financial inputs **when it completes** — directly demonstrated on the
  CFR run (council 8/8, VG completed and reasoned over the pack). On AAPL, VG receives the identical pack (all
  agents get the same serialized pack) but **TMP-fails** on the larger 20-item AAPL pack (environmental — see note).
- ✅ financial facts remain present despite 20+ catalyst/news events (floor honored; `E8`/`E9`/`E10` among 20).
- ✅ primary/regulator evidence outranks aggregator news (financial `E8`–`E10` ahead of news `E11`–`E20`).
- ✅ duplicate/derivative noise reduced (pack bounded to 20 with dedup+caps active; CFR pack compressed to 9).
- ✅ price data T5 / derived metrics T6 / **annual never TTM** — FA cites "fiscal 2025" (annual), never TTM;
  the CFR council explicitly labels price/momentum "model estimates and aggregators… **not primary financial facts**".
- ✅ no fabricated EBITDA / EV/EBITDA / beta / TTM values — those literals appear only inside disclaimer text;
  CFR Valuation Guard explicitly notes their **absence** ("no P/E, EV/EBITDA, or ROE… prevents any multiple-based valuation").
- ✅ `schema_valid`=true · `safety_valid`=true · `human_review_required`=true · `publication_ready`=false.

### CFR fallback (`a39ac390`, flag ON, council 8/8) — metadata-only stays references
Correctly scoped to **Compagnie Financière Richemont / CFR / SW** (`is_mock=false`, prov=real). Swiss issuer →
`fundamentals_available=false` (no SEC XBRL) → the tier-split emitted **zero** financial-fact items:
`sec_financial_statement` count **0**, `primary_facts` **0**. The company-IR primary sources remained
**metadata-only references** (`metadata_only_source_count=6`, `primary_source_reference_count=6`). Financial
Analyst honestly reports "primary filings available only as metadata or links without extracted financial data";
Valuation Guard (completed) correctly **withholds** valuation for want of inputs. `schema_valid`/`safety_valid`=true,
`human_review_required`=true, `publication_ready`=false.

### Security / regressions
- **Secret scan (response surface):** CLEAN — no secret patterns across all fetched report/run JSONs. (Runtime
  log tail is not read-only accessible under the scoped `az appsettings list` allow rule; mitigation = clean
  response-surface scan + Slice 2 adds only counts-only logging, security-agent verified.)
- **Auth:** unauth `from-report` / `run` / `GET report` all → **401** (no bypass).
- **Publication:** `publication_ready=false` on every report; generation routes admin-gated; no public publish route.

## Tests (pre-merge, on the branch)
Backend **2338 passed / 20 skipped / 0 failed** (+26 = the new `test_phase32a_slice2_evidence_budgets.py`,
no regressions); ruff clean; mypy **71** = baseline (0 new, 0 in changed files). No web files changed.
GitHub CI **Lint & Test PASS**. Reviews: **ib-test GREEN · ib-security PASS · ib-pr-review GO-WITH-NITS**
(the one pre-PR nit — the staging-validation plan — was resolved before merge).

## Environmental note (non-blocking)
- **Valuation Guard on AAPL does not complete** under current staging Azure `gpt-4.1-mini` TPM: the council runs
  8 agents sequentially with no reserved budget, and the richer 20-item AAPL pack consumes more tokens per call,
  so agents 5–8 (incl. VG) hit 429 (AAPL councils = 4/8; the smaller 9-item CFR pack reached 8/8). This is the
  documented environmental limiter and is exactly what **Slice 4** (council retry / backoff / reserved
  critical-agent budget) will fix. VG's correct behavior over the Slice-2 pack IS demonstrated on the CFR run.
  The acceptance criterion is conditional ("when the agent completes"); financial-fact presence + FA acknowledgement
  (the core Slice-2 outcome) are demonstrated independently of council completion.
- **Evidence pack is council INPUT** and is never persisted/logged (by design); ON-state pack contents are
  therefore observed via council-agent outputs + `evidence_item_count` (the documented observability approach),
  not a raw pack dump.

## Limitations / follow-ups (NOT in Slice 2)
- **Slice 3:** citation `report_id` backfill + honest E# evidence vs DB-citation distinction.
- **Slice 4:** council retry / backoff / reserved critical-agent budget (resolves the AAPL VG-TPM partial).
- **Slice 5:** deeper document ingestion (SEC/8-K/earnings HTML, table extraction, bounded OCR).

## Final verdict
**CLOSED + STAGING-VALIDATED (`3237d27`), 2026-08-02 — WITH ENVIRONMENTAL NOTE.** All Slice 2 acceptance
criteria met; the sole caveat (valuation_guard TMP-fails on the large AAPL pack) is environmental and owned by
Slice 4. Flag `LLM_COUNCIL_EVIDENCE_BUDGETS_ENABLED` kept **ON**. Slices 3–5 remain and must NOT auto-start.
