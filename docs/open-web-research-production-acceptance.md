# Open-web research — production deployment and acceptance record

> **Status (2026-10-07): COMPANY RESEARCH VALIDATED LIVE; DISCOVERY ADMISSION BY SEARCH NOT MET.**
> The open-web path runs in production end to end (Tavily → fetch → extract → corpus → retrieval →
> council findings with citations) and passes the company-research, regression, whitepaper and
> recent-news cases. Discovery performs real web search and surfaces genuine long-tail companies, but
> **no company has been admitted to a shortlist by search** (criteria A and D). The remaining cause is a
> trust-policy choice for the owner (see §9), not an unfixed defect. Section 8 is the verdict table.

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

### Rainbow Rare Earths — run 2 (job `628aa35e`, report `f6ab989f`, after #268) — PARTIAL

The retrieval fix worked: the report now carries the issuer's current project facts that run 1
lacked — Interim Economic Study (post-tax NPV10 US$611m, IRR 38%, US$326m capital), press-reported
capex of US$325–350m with ~two-thirds debt, ~1,850 t/yr separated REO, an 85% interest in
Phalaborwa and a 49% share of Uberaba, Phalaborwa "initial production H1 2029" and Uberaba "initial
production late 2030". The ownership gap is gone.

Two further generic defects, both in the shared field vocabulary (`research_fields.py`), remained:
1. **Coexisting gap and finding:** "No planned annual output tonnage" stayed open (`field_unknown`)
   while a finding stated "targeting ca. 1,850t/yr" — the gap's wording named no field, and "ca."
   split the finding's clause.
2. **No temporal supersession:** "extraction targeted from 2028" (April) and "initial production H1
   2029" (September) are one milestone with two dates, but neither wording named a tracked field, so
   `supersessions` was 0 and both read as current.

*Independent review (NO-GO on the first version) found it over-closed:* a historical rate, a peer's
target, market demand, plant feed, exports/sales and emissions-reduction rates all became "stated
capacity", and "initial production in 2019 delivered 5kt" became a 2019 target that could be
superseded by a 2029 one. Corrected before merge: a planned-rate pattern needs a planning word, a
short comma-free window that cannot cross offtake/demand/feed/sales/peer/market/actual wording, and
a clause not about peers or the market; "output tonnage" / "tonnage, capacity" now identify what a
*gap* asks for and can never make a *finding* state capacity; "initial/first production" is not a
target cue when the clause reports what happened. Each case is a regression test.

Fix: planned-output tonnage / rate wording names `metric:production_capacity`; "initial production",
"start of extraction/mining" and "extraction targeted/aimed…" name `milestone:first_production`;
"initial production" is a target cue (but not a *former* target); the clause splitter no longer
breaks after "ca.", "approx.", "est.", "incl.", "vs.". Tests: `tests/test_research_fields_planned_output.py`.

### Rainbow Rare Earths — run 3 (job `63a610ea`, report `0946f29d`, after #268 + #269) — PARTIAL

Current project facts present: Phalaborwa capex US$325–350m (press, ~two-thirds debt, US$50m DFC),
Interim Economic Study (NPV10 US$611m, IRR 38%, US$326m capital), Uberaba EA 11 Mar 2026
(NPV10 US$916m, IRR 45%), Neo MOU shares (40% NdPr oxide / 65% mixed heavy REC), 85% interest,
~1,850 t/yr, and an explicit finding that first production "moved from 2028 to H1 2029".

Remaining generic defects found by reading the reconciler's behaviour on the real statements:
1. `supersessions`/`temporal_disagreements` were both 0: "moved from 2028 to H1 2029" extracted only
   2028 (so it looked like a restatement), and "No Rainbow project is in construction" gave the
   finding a project key `no rainbow`. Fix (this PR): a stated change targets only the new date;
   sentence-initial "No/not/none…" is not a project name. By design a `secondary_web` statement of the
   move is recorded as a `non_issuer_source` **disagreement**, not a supersession — only issuer-primary
   sources order guidance.
2. **Known limitation, not changed:** the gap "No planned annual output tonnage" stays open while a
   finding states "project life ca.16 years, ca.1,850t/yr separated magnet REO". The finding has no
   planning word, so it cannot be told from an actual rate; closing it would let an actual or a peer's
   rate close a capacity gap (the review of #269 showed that is the worse error).

### Discovery — critical minerals, run 1 (`0bdd19bb`, durable job `6eec7042`) — NOT ACCEPTED (criterion A)

Real web path proven: 24 Tavily queries executed (HTTP 200, request ids, 24.0 credits), 184 results,
26 pages fetched / 24 ingested, 16 company mentions → 15 leads; durable job completed
(`completed_with_warnings`, attempt 1). *An earlier run (`77adbbfd`) was orphaned because an app-setting
restart landed after it was created; the in-process path cannot resume, which is what the durable job
fixes — a sequencing mistake in the rollout, not a product defect.*

**Fails A:** the 3 returned companies (PRE, RBW, ERA) all came from the curated registry;
`web_admitted: 0`, `recall_verification: attempted 6, corroborated 0`. Measured causes:
1. **Corroboration was starved.** It runs last on the run's own ceilings: the main plan used 20 of 24
   queries and 36 of 40 fetches, so only 4 of 6 verification queries could be issued and 4 fetches
   remained for ~12 wanted pages. Fix: a bounded allowance of its own (+1 query and +3 fetches per
   recalled lead; an operator's `V3_RUN_MAX_WEB_SEARCHES` stays hard).
2. **Rule A3 could never pass for a mining company.** A3 needs a passage from an acceptable source
   class; every mining trade page fetched (news.metal.com, panorama-minero.com, rareearthexchanges.com …)
   classified `unknown_web`. Fix: established mining trade titles added to the curated list
   (`CURATED_LISTS_VERSION 2026-10-07.1`); promotion-heavy junior-stock sites and lookalike hosts are not.

Evidence the gates work as designed: search found **Anson Resources (ASX: ASN)** via a neighbouring
company's project page; A1 (search provenance), A2 (ASX directory listing) and A4 passed, A3 correctly
failed (a competitor's own website is not independent evidence about Anson).

### Discovery — critical minerals, run 2 (`b2ad234a`, after #271) — NOT ACCEPTED (criterion A)

All 6 verification queries now ran (`no_search_provenance` 11 → 6). Search found real ASX names —
**Lynas Rare Earths, Arafura Rare Earths, Iluka Resources, Hastings Technology Metals, IGO, Mineral
Resources** — with A1 (search provenance) and A2 (ASX directory) passing and a theme term in each stored
passage, but **all six came from one page on farmonaut.com**, an SEO "top ASX miners" list on a
satellite-imagery vendor's blog, correctly classed `unknown_web`. Rule A3 therefore (correctly) refused
to admit six companies on one promotional list; `web_admitted` stayed 0 and the 3 returned companies
were again the curated registry's.

**Cause:** search yield, not the gate. The generic entity queries return SEO lists. **Fix (this PR):**
one entity query is restricted (`include_domains`) to the classifier's own curated trade-press list, so
results are pages A3 can accept. The restriction is exactly `trade_publication_hosts()`.

### Discovery — runs 3 and the grid niche (`29a088cd`, `44371d41`, after #272) — NOT ACCEPTED (criterion A)

The trade-press query worked mechanically (`trade_publication` pages 0 → 2) but `web_admitted` is
still 0 in both runs, and `recall_verification` is `attempted 6, corroborated 0` in every run. Tracing
the grid niche's verification queries found two further causes:

1. **Date-filtered name checks (fixed here).** The six verification queries inherited the planner's
   3-year date window, so three of them ("Prysmian S.p.A. …", "ABB Ltd …", "Eaton Corporation plc …")
   returned nothing: a company's own pages are undated and the filter drops them.
2. **A design limit, not a defect — OWNER DECISION NEEDED.** The mention extractor recognises a
   company only *as a listed company* (a name beside a ticker, venue or ISIN, or a legal-form name with
   the venue in the same paragraph). A company's own page (nexans.com, siemens-energy.com — both were
   fetched and ingested) never prints its own ticker, so it can never corroborate itself; and the
   issuer's own site is `unknown_web` because no issuer domain is verified for a recalled lead, so rule
   A3 refuses it. Letting an issuer-own-site page count would need a rule for trusting an issuer domain
   (e.g. registrable label equals the directory-verified name). That widens what a page can claim about a
   real company and is a trust-policy choice, so it has **not** been made here.

**Where Discovery stands:** the real-web machinery works end to end (Tavily executed, pages fetched and
ingested, mentions extracted, identities verified against the ASX/LSE/Euronext directories, durable
job completes). Search surfaces genuine long-tail names that no hard-coded list holds (Anson Resources,
Ioneer, Lynas, Arafura, Iluka, Hastings, IGO, Mineral Resources), shown as *also surfaced* with the
exact rule that stopped each. **No company has yet been admitted to a shortlist by search**: the shortlists
were the curated registry's. Criterion A is therefore not met, and is not claimed.

## 8. Final verdicts (live evidence only)

| Case | Verdict | Evidence |
|---|---|---|
| A critical-minerals Discovery, search-admitted shortlist | **NOT MET** | 3 runs; `web_admitted: 0`; shortlists were the curated registry's (PRE, RBW, ERA). Real long-tail names surfaced and shown as *also surfaced* with the rule that stopped each (Anson Resources, Ioneer, Lynas, Arafura, Iluka, Hastings, IGO, Mineral Resources). |
| B Rainbow Rare Earths | **PASS with limits** (run 3 `63a610ea`) | Phalaborwa capex US$325–350m (press) with debt/equity split, Interim Economic Study (NPV10 US$611m, IRR 38%, US$326m capital), Uberaba EA 11 Mar 2026 (NPV10 US$916m, IRR 45%), 85% Phalaborwa / 49% Uberaba, ~1,850 t/yr, first production moved 2028 → H1 2029 (recorded). **Limit:** "No planned annual output tonnage" gap stays open while a finding states ~1,850 t/yr without a planning word — deliberately not closed (over-closure is the worse error). |
| B Pensana | **PASS** (run 3 `d696dbdd`) | 6 Aug 2026 "Update on Cascade financing" (US$165m; US$15m received), Longonjo 22% built / ~US$135m of ~US$250m committed, production 2027, Stage 1 20,000 t/yr → Stage 2 40,000 t/yr, Saltend refinery scrapped. Older guidance is single-valued in the evidence, so no supersession arises. |
| B EcoGraf | **PASS** (run 3 `30dc4423`) | 30 Sep 2026 SPP close, KfW IPEX mandate (up to US$105m), EIB grant, offtake 40,000 tpa incl. ThyssenKrupp, Mitsubishi Chemical MOU, SML 733/2025; honest state "FY2026 annual report acquired — figures not yet extracted". |
| C Southern Copper | **PASS** (`ba5782f7`) | FY2025 Group revenue US$13,420.0m from SEC, OCF/capex/FCF consistent, cash conversion 109.3% = 4,752.1 ÷ 4,348.2, cash not presented as liquidity. Limit: one finding says "no interim revenue" while another cites Q2 2026 net sales. |
| C Cleveland-Cliffs | **PASS** (`cee90510`) | SEC FY2025 canonical; H1 2026 net income (363) captured from the 10-Q as interim with "not comparable, never annualised"; cash US$57m vs "cash plus ABL liquidity US$3.3bn" kept apart; steel ASP and "+US$240m on pricing" stated factually, no "pricing power" claim. |
| D additional niches | **NOT MET** (grid `44371d41`, automation `8ca9e615`) | Same pattern: curated shortlists, recalled leads uncorroborated. Medical-imaging, ASX and UK small-cap niches were not run: the cause is shared and unchanged. |
| E whitepaper / PDF path | **PASS** | Pensana finding `4742893c` cites a corporate-presentation PDF (p.3) reached by a `company_docs` search (request id present) → `application/pdf` 200 → extraction → corpus chunk → finding `ev:c:`; SCCO 10-K PDF (573 chunks), CLF annual report PDF (399), a University of Texas PDF via `competitive`. |
| F recent news | **PASS** | Finding `27e3ef08` rests on the issuer's 6 Aug 2026 release (found by a `risk` search, fetched from `news.cision.com`, class `company_press_release`, dated 2026-08-06); also rareearthexchanges.com 2026-06-08 and a BBC article 2025-10-16. |
| G provider outage | **PARTIAL** | Real Tavily returns HTTP 401 to the production adapter (`executed=False`, `error_code='auth'`, one counted call, no results); the orchestrator test asserts `web_search_unavailable`, the "Live web search unavailable" label, recalled leads staying `model_recall`, no fetch. A production outage was NOT induced: that needed overwriting the live credential, which the safety classifier denied. |
| W7 follow-up + red-team wave | **PASS** (Rainbow `045abb7e`) | 6 gap queries, 9 pages fetched / 8 ingested, 1 challenge resolved, 4 risk-evidence items; report grew to 62 findings. A mid-run restart was recovered by the durable job (attempt 2). |

## 9. Owner decisions and known limitations

1. **Issuer-domain trust (decides A and D).** A company's own page cannot corroborate it (the mention
   extractor only recognises a company *as listed*, and the issuer's site is `unknown_web` without a
   verified domain). Admitting search-found companies at the rate the brief expects needs a rule for
   trusting an issuer domain, e.g. registrable label equals the directory-verified name. That widens what
   a page can claim about a real company, so it was not made unilaterally.
2. `V3_DEEPSEEK_SEARCH_ENABLED` is still ON: the replacement is not fully accepted, so it was not retired.
3. App-setting changes restart the app 5–8 minutes later; confirm the process start time, not `/health`.
4. FCA NSM and ASX sources remain private-use only.

## 10. Production record

- **Migration head:** 044 (single head; no migration after #264). **Deployed API/web SHA:** `742047c` (main).
- **Flags ON:** `V3_WEB_SEARCH_ENABLED`, `V3_WEB_SEARCH_PROVIDER=tavily`, `V3_WEB_FETCH_ENABLED`,
  `V3_WEB_CORPUS_INGEST_ENABLED`, `V3_COMPANY_WEB_RESEARCH_ENABLED`, `V3_DISCOVERY_DURABLE_ENABLED`,
  `V3_DISCOVERY_WEB_SEARCH_ENABLED`, `V3_WEB_FOLLOWUP_ENABLED` (with `V3_DURABLE_JOBS_ENABLED`).
  `TAVILY_API_KEY` is configured (value never read back).
- **Tavily:** 531 query rows (529 executed), **301 real network calls = 301 credits** (the rest served from
  the same-day cache), 3,824 results; 140 calls on 2026-10-06 and 161 on 2026-10-07 against the 300/day cap.
  Dollar cost is read from the Tavily dashboard (not derivable from the credit count here).
- **Fetch/ingest:** 2,295 fetch attempts (1,156 fetched; failures all explicit: 403, captcha, paywall, robots,
  TDM), 368 web documents, 11,280 chunks.
- **Implementation PRs (merge SHA):** #257 `faac37c`, #263 `80f4971`, #260 `417f090`, #261 `99953af`,
  #264 `255e5d4`, #265 `2445fa7`, #262 `0039bf7`, #258 `4ba3950`, #266 `9730abd`.
- **Corrective PRs found by live acceptance:** #268 `94af591` (corpus search: subject-name stripping,
  document diversity, page of 12), #269 `1a1b7ea` (planned-output / first-production wording, abbreviation-safe
  clauses; review caught over-closure), #270 `2ecc450` (stated change targets the new date; "No" is not a
  project), #271 `57579bd` (bounded corroboration allowance; mining trade press), #272 `927a23b`
  (trade-press-restricted entity query), #273 `742047c` (verification queries not date-filtered).
- **Live run ids:** company jobs Rainbow `54ef37ec`, `628aa35e`, `63a610ea`, `045abb7e`; Pensana `b3f01000`,
  `0e8cb05e`, `d696dbdd`; EcoGraf `0311645d`, `ef3d284f`, `30dc4423`; SCCO `ba5782f7`; CLF `cee90510`.
  Discovery runs `0bdd19bb`, `b2ad234a`, `29a088cd` (critical minerals), `44371d41` (grid), `8ca9e615`
  (automation); `77adbbfd` was orphaned by a settings restart.
- **Tests:** integration gate 9,708 passed / 41 skipped / 0 failed (before the live correctives); corrective
  suites 949, 646, 654, 328, 1,427 and 111 passed; every merged PR's CI green on its exact head.

### Discovery — critical minerals, run 4 (`0146d2ce`, after #275's A3 change) — web stage starved

With the corrected A3 deployed, run 4 found **no web leads at all** (`web_leads: 0`): the stage's
wall-clock budget (360 s) was spent after only 12 fetch attempts — three slow PDF extractions (market-report
PDFs from the `document` queries, which rarely name a listed company) consumed it, so the entity and
trade-press pages that name companies were never read ("fetching stopped: budget:max_wall_seconds").
Generic fix: fetch web pages before PDFs (each group in its own selection order) and skip further PDFs once
half the wall budget is spent.

### Discovery — critical minerals, run 5 (`8fc0ec31`, after #276) — the issuer's own page was never read

The web stage now reads 26+ pages and surfaces many real search leads (42), all passing A1 and A2: Mineral
Resources, IGO, Arafura, Argosy Minerals, Core Lithium, Delta Lithium, Hastings, Iluka, Lynas, Altair
Minerals, Andean Silver. **One was admitted on independent evidence** (Lithium Argentina, theme `independent`,
corroboration `independently_corroborated`) and then correctly **excluded by the geography constraint**
(Canada). Official domains were established for IGO (`igo.com.au`), Arafura (`arultd.com`) and Lynas
(`lynasrareearths.com`) from exchange announcement letterheads — and they still failed A3, because the
platform never fetched those issuers' own pages: nothing in the search results was an issuer page, and
nothing asked for one. Fix: once an official domain is independently established, the platform reads that
issuer's official page (same open-web policy, must stay on the official domain after redirects, ingested into
the corpus so the passage is citable) and records its theme passages as issuer-origin evidence.

### Discovery — critical minerals, run 6 (`f51e6edd`, after #277) — official pages refused by the fetch ceiling

Official domains were established for five search leads (IGO `igo.com.au`, Anson `ansonresources.com`,
Arafura `arultd.com`, Ioneer `ioneer.com`, Lynas `lynasrareearths.com`) and the reader tried each issuer's
page — and every read was refused `budget:max_fetches`: the plan and the corroboration allowance had already
spent past base + targets. The reader's allowance is now one page per target beyond what the run has already
fetched (still bounded by the number of targets).
