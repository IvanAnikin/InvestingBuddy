# Open-web research — production deployment and acceptance record

> **Status (2026-10-06): LIVE ACCEPTANCE IN PROGRESS — NOT ACCEPTED.**
> `TAVILY_API_KEY` is configured; search, fetch, corpus ingest and company web research are ON
> in `ib-stg-api` (Discovery search, follow-up and durable Discovery are still OFF). Rainbow Rare
> Earths run 1 exposed a retrieval defect (§7); the fix is in review. No acceptance case is
> claimed passed. This file is the honest record and is updated as each case is run.

Specification: [`open-web-research-spec.md`](open-web-research-spec.md) ·
plan: [`open-web-research-implementation-plan.md`](open-web-research-implementation-plan.md) ·
acceptance cases: [`open-web-research-acceptance-plan.md`](open-web-research-acceptance-plan.md).

## 1. What is deployed

| PR | Merge SHA | Content |
|---|---|---|
| #257 | `faac37c` | Specification, threat model, provider evaluation (docs only) |
| #263 | `80f4971` | W0 fetch hardening (D1–D14) + W1 search provenance + Tavily adapter (migration 042) |
| #260 | `417f090` | Track B: final gap reconciliation, temporal supersession, "0 verified" semantics (migration 043) |
| #261 | `99953af` | Track C: non-US structured financials tri-state, development-stage playbook (pipeline v19) |
| #264 | `255e5d4` | W2 public-web fetch policy + W3 web documents in the corpus (migration 044) |
| #265 | `2445fa7` | W4 source trust / corroboration / packs + W5 company-research web stage |
| #262 | `0039bf7` | W6a Discovery on the durable worker |
| #258 | `4ba3950` | Exact, bookmarkable Discovery run routes `/research/discover/[run_id]` |
| #266 | `9730abd` | W6b Discovery live search, W7 follow-up loop + red-team wave, W8a admin audit page, W8b investor UX |

Integration gate (`integration/open-web` f94c055, before the merges): full pytest
9,708 passed / 41 skipped / 0 failed (`ENABLE_INTEGRATION_TESTS=false`), ruff clean, mypy at its
71/11 baseline, web `tsc` + `eslint` clean. Each merged PR additionally passed its own CI on the
exact merged head. #266 web e2e (discovery routes + W6b + W8b): 48/48 (4 parallel-run
`ECONNRESET` flakes re-run serially 13/13).

**Migration head: 044** (`041 → 042 web-search provenance → 043 report reconciliation → 044
web-document/corpus support`), applied manually to production by `alembic upgrade`, single head.
Schema-readiness endpoint reports migration 043 ready; the admin web-research audit route answers
404 for an unknown id (tables exist). #266 carries no migration.

## 2. Feature flags

All open-web flags are **absent / default OFF** in production (names only, no values):
`V3_WEB_SEARCH_ENABLED`, `V3_WEB_SEARCH_PROVIDER` (`none`), `V3_WEB_FETCH_ENABLED`,
`V3_WEB_CORPUS_INGEST_ENABLED`, `V3_COMPANY_WEB_RESEARCH_ENABLED`,
`V3_DISCOVERY_WEB_SEARCH_ENABLED`, `V3_WEB_FOLLOWUP_ENABLED`, `V3_DISCOVERY_DURABLE_ENABLED`.
`V3_DEEPSEEK_SEARCH_ENABLED` (legacy, documented upstream as "Ignored") is still ON and is to be
retired only after the replacement is accepted. Search budget: 300 queries/day platform cap
(`V3_WEB_SEARCH_MAX_QUERIES_PER_DAY`), public-context queries only.

## 3. Blocker

`TAVILY_API_KEY` is absent from the `ib-stg-api` app settings (re-checked 2026-10-06, setting
names only; the value is never read or printed). The Key Vault `ib-stg-kv` is not readable by the
Contributor-only CLI identity, but a Key Vault reference would still appear as an app setting
named `TAVILY_API_KEY`; none exists. With no provider, enabling any search flag would only
produce explicit `web_search_unavailable` states.

## 4. Acceptance cases — status

| Case | Status |
|---|---|
| A critical-minerals niche Discovery (query → Tavily execution → ranked result → fetched source → thematic evidence → official listing verification → admission) | NOT RUN |
| B Rainbow Rare Earths / Pensana / EcoGraf fresh deep runs, full read-through | NOT RUN |
| C Southern Copper / Cleveland-Cliffs regressions | NOT RUN |
| D additional niches (EU grid/data-centre, industrial automation, medical imaging, ASX niche, UK small-cap) | NOT RUN |
| E whitepaper path | NOT RUN |
| F recent-news path | NOT RUN |
| G provider outage → explicit `search_unavailable`, recall never labelled search | NOT RUN live (unit/e2e covered: `w6b-discovery-web-search.spec.ts`) |

No Tavily network-call proof, query/fetch/cost totals, Discovery run IDs, company report IDs or
long-tail discoveries exist yet; none are claimed.

## 5. Rollout to execute once the key is configured (one flag group at a time)

1. Verify schema readiness (head 044) and deployed API/web SHAs.
2. Set `TAVILY_API_KEY` (Key Vault reference), `V3_WEB_SEARCH_PROVIDER=tavily`,
   `V3_WEB_SEARCH_ENABLED=true`; keep consumers off. Tiny live smoke; confirm a
   `web_search_queries` row with `executed=true`, a Tavily request id and a 2xx.
3. `V3_WEB_FETCH_ENABLED`, `V3_WEB_CORPUS_INGEST_ENABLED`; then `V3_COMPANY_WEB_RESEARCH_ENABLED`;
   run one company; read the full report.
4. `V3_DISCOVERY_DURABLE_ENABLED`, `V3_DISCOVERY_WEB_SEARCH_ENABLED`; run one Discovery; read the
   full result.
5. `V3_WEB_FOLLOWUP_ENABLED`; then cases A–G; any live defect follows
   generic root cause → regression test → fix → review → PR → CI → merge → deploy → re-run → re-read.
6. Retire `V3_DEEPSEEK_SEARCH_ENABLED`; record actual Tavily cost (expected cap ≈ USD 60/month).

## 6. Known limitations

- Acceptance is unproven against the real provider; defects visible only live are still unknown.
- FCA NSM and ASX sources remain private-use only (see project terms decision).
- Admin web-research audit and investor UX are verified against fixtures, not live data.

## 7. Live acceptance log

### Rainbow Rare Earths — run 1 (job `54ef37ec`, report `6a6022fb`, deep) — NOT ACCEPTED

Open-web path proven end to end: 33 queries, **33/33 executed** (Tavily HTTP 200, request ids,
33.0 credits), 231 results, 154 fetch attempts (70 fetched, failures all explicit: 403, captcha,
paywall, TDM reservation), 27 web documents → **840 chunks, all indexed**, 8 pages from the
issuer's own site, council convened with 47 findings, current dated facts captured (Phalaborwa
start 2028 / 16-year life / 35 Mt dunes, US$50m DFC funding, Neo MoU, Uberaba PFS commenced
2026-09-07).

**Fails criterion B:** the report still lists "no ownership percentage", "no planned production
capacity", "no output tonnage" as open gaps, and no capex figure appears. The ingested issuer
presentation states all of them (an 85% interest, ~1,850 t/yr of NdPr/Dy/Tb, US$295.5m capex), and
an Uberaba economic assessment dated March 2026 (NPV US$916m, IRR 45%) was fetched.

**Root cause (measured by replaying the investigator's own queries against the live corpus):**
retrieval ranking, not ingestion. The chunks holding those figures ranked 12th–24th, and the
investigator reads one search of 8 hits per question.
1. The subject's own name in a company-scoped query is the loudest lexical term, so every chunk
   that repeats it outranks the passage that answers. Dropping it moved the key chunks to ranks
   7 / 5 / 4 (ownership / economics / capex).
2. One document filled the page (5 of the first 8 hits were one filing). A cap of two chunks per
   document plus a default page of 12 puts every key chunk inside the page (ranks 8 / 12 / 5 / 4 / 11).

**Fix (retrieval-only, no schema change, no re-ingest):** `agent_tools/corpus_ranking.py` —
single-company queries drop the subject's name (Unicode-aware; a one-word name only where it is
written as a name; also recognised after a keyword reducer has dropped "of"/"Group"), the candidate
pool is wider than the page, each document's best two chunks come first and the remainder is
*demoted, not dropped* (a one-filing company such as SCCO still gets a full page), and the
investigator's page is 12 hits at every corpus rung. The company filter and every access rule are
unchanged. Tests: `tests/test_v3_corpus_search_ranking.py`.

*Independent review caught two defects in the first version of this fix, both corrected before
merge:* the investigator always names its own `top_k`, so changing only the tool's default changed
nothing on the live path; and a hard per-document cap cut a one-filing company to two hits.

**Recorded but not yet fixed:** 13 of the 27 web documents never name the company (357 of 840
chunks; e.g. one government document of 224 chunks) yet are scoped to it with subject method
`research_run`, so they compete in company-scoped searches; and the issuer's own site is classed
`unknown_web` because no official domain is known for the company. Both are re-assessed after the
re-run of the fix.
