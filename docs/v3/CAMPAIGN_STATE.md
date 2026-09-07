# InvestingBuddy V3 — Campaign State

**This file is durable campaign memory. Read it first, before any other V3
document, at the start of every session and in every delegated subtask.**

It records what is *true of the repository right now* — verified against Git and
the code, not inferred from a plan. When it disagrees with a phase-gate section in
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), the plan is the historical
record of a gate and this file is the current state; re-verify before trusting
either.

## DARK DEPLOYED — 2026-09-07

**V3 is live on `main` and every V3 feature is off.**

| Item | Value |
|---|---|
| `main` | **`5826f61`** (PR #189, merged 19:34Z) |
| Deployed API | `5826f61`, build 34156081666, `/health` ok |
| Live database | alembic **038**, 23 → 61 tables |
| V3 app settings in production | **0** — every flag takes its code default, which is off |
| DeepSeek settings in production | **0** — no credential present |
| V3 API paths exposed | **none** (68 paths, unchanged) |
| V3 activity in logs | **none** — no worker, no pipeline, no provider call |
| `release/v2-current` / V2 tag | `4b60e07` / `7d9aa12` — untouched |

**The ordering was load-bearing and it held.** The live database was migrated
018 → 038 while V2 code was still serving, and V2 then passed a full smoke test on the
migrated schema *before* any V3 code was deployed. The reverse order would have been a
product-wide outage: V3's ORM declares `companies.legal_entity_id` and names it in every
`SELECT`, and six API modules read `Company`.

**Rollback is a redeploy, not a migration.** V2 ran on schema 038 in production for the
whole deploy window and served correctly, so `release/v2-current` can be redeployed with
no down-migration. A pre-migration `pg_dump` (18 MB, 185 TOC entries) and PITR with 7-day
retention are the second and third routes.

**CI's first run over the complete V3 change set failed**, and correctly: 13 tests were
asserting that `langchain-openai` is installed, which it is not under CI's `[dev]`-only
install. Fixed in V3.12.1 at the `_BUILDERS` seam with no test weakened, and pinned by a
structural guard. Green on the second run: **6,119 passed, 49 skipped**.

**Not activated, and gated:** `V3_PIPELINE_ENABLED` stays false. Broad reader-facing
activation still waits on [OPEN DECISION #23](OPEN_DECISIONS.md) (deterministic
forbidden-language backstop for V3 output) and on finite, *enforced* `ResearchBudget`
limits — the run budgets currently default to unbounded and `ResearchBudget.check()` is
not called anywhere in the V3 pipeline.

**Release readiness:** see
[V3_RELEASE_AND_DARK_DEPLOYMENT_REPORT.md](V3_RELEASE_AND_DARK_DEPLOYMENT_REPORT.md) —
**READY FOR MAIN MERGE + DARK DEPLOYMENT**, conditional on migrating the live database
**before** deploying V3 code. Measured on real PostgreSQL 16.15: V3 code on an 018 schema
**fails** (`companies.legal_entity_id` does not exist) while V2 code on a 038 schema is
fine, so the briefed order (merge → deploy → migrate) would cause a product-wide outage
and must be inverted.

**Last verified:** 2026-09-07, after V3.12 merged at **`a2f0f4f`**, by direct `git`
inspection, a full local gate run (API and web), a full migration chain up-and-down
against real PostgreSQL 16, a live real-document corpus acceptance run, a **live DeepSeek
`/responses` contract run (16/16)**, and an independent code review plus an independent
security review of the slice diff.

Protected refs re-verified at that point: `main` = `release/v2-current` =
`v2-final-pre-v3-2026-09-04^{commit}` = **`4b60e07`**, `git rev-list --count
develop/v3..main` = **0**, and the deployed API still reports `commit_sha 4b60e07`
(build 2026-09-02). Nothing was merged to `main`, deployed, or migrated.

---

## Protected baseline — verified, not assumed

| Ref | SHA | Verified how |
|---|---|---|
| V2 baseline | `4b60e07ebffa42979a31c1720db794f606c15e53` | `git rev-parse main` |
| V2 tag `v2-final-pre-v3-2026-09-04` | tag object `7d9aa12`, dereferences to `4b60e07` | `git rev-parse v2-final-pre-v3-2026-09-04^{commit}` |
| `release/v2-current` | `4b60e07` | `git rev-parse release/v2-current` |
| `main` | `4b60e07` | equal to `origin/main`; `git rev-list --count develop/v3..main` = **0** |

**No V3 commit is on `main`.** `git rev-list --count main..develop/v3` = 52 in the
one direction and 0 in the other, which is what "V3 is strictly ahead and `main`
is untouched" looks like.

Forbidden for the whole campaign, and not authorised by any prompt in it: merging
V3 to `main`, deploying V3, applying a V3 migration to the deployed database,
enabling a V3 flag in production, deleting V2 compatibility or either V2 ref.

## V3 state

| Item | Value |
|---|---|
| `develop/v3` HEAD | **`a2f0f4f`** (V3.12 merged 2026-09-07), ahead of `origin/develop/v3` (`fd82d3d`), unpushed |
| Alembic head in source | **038** (`038_add_monitoring`) |
| Alembic head in the deployed database | **018** — and V3 migrations 019-038 have reached **no** deployed environment |
| Alembic head in the local dev database | **018** (unchanged by V3 work; scratch databases only) |
| Current phase | **V3.11 production hardening complete.** V3.0-V3.11 all `IMPLEMENTED`; all four V3.10 blockers closed; the recommendation is **`READY FOR USER ACCEPTANCE / MAIN PROMOTION REVIEW`** — see [V3_RELEASE_CANDIDATE_REPORT.md §12](V3_RELEASE_CANDIDATE_REPORT.md#12-recommendation) |
| Current slice | none — awaiting user acceptance |
| Deployed | **Nothing.** `main` at `4b60e07` is the deployed product. |

Working tree at campaign start also held two untracked files —
`docs/DATA_SOURCE_INVENTORY.md` / `.xlsx`. They are
[OPEN DECISION #18](OPEN_DECISIONS.md#18-fate-of-docsdata_source_inventorymd--xlsx),
the user's call, and the campaign left them untracked and untouched.

**As of 2026-09-07 neither file is present in the working tree.** No campaign commit
removed them — they were never tracked, so nothing here could have. Recorded as an
observation rather than an explanation; decision #18 remains the user's.

## Phase status

| Phase | Goal | Status |
|---|---|---|
| V3.0 | Execution and correctness foundation | `IMPLEMENTED` |
| V3.1 | Research Corpus | `IMPLEMENTED` |
| V3.2 | Entity Master and global universe | `IMPLEMENTED` — all six slices merged; [phase gate](IMPLEMENTATION_PLAN.md#11-v32-phase-gate) |
| V3.3 | Research tools and calculation engine | `IMPLEMENTED` — all four slices merged; [phase gate](IMPLEMENTATION_PLAN.md#12-v33-phase-gate) |
| V3.4 | Multi-provider runtime and source expansion | `IMPLEMENTED` — eight slices merged; 4.2/4.6 `DEFERRED` by user decision; [phase gate](IMPLEMENTATION_PLAN.md#13-v34-phase-gate) |
| V3.5 | Research Ledger, Director and bounded loop | `IMPLEMENTED` — 5.1/5.2/5.3 merged |
| V3.6 | Industry playbooks | `IMPLEMENTED` — 6.1/6.2 merged; five materially different playbooks |
| V3.7 | Council V2 and Red Team | `IMPLEMENTED` — 7.1/7.2 merged |
| V3.8 | Research Memory and Delta | `IMPLEMENTED` — 8.1/8.2 merged |
| V3.9 | Monitoring | `IMPLEMENTED` — 9.1 merged, feature-gated, **nothing schedules it** |
| V3.10 | End-to-end integration and real-issuer acceptance | `IMPLEMENTED` — 10.1-10.4 merged + 2 correctives |
| V3.12 | External research integration and final acceptance | `IMPLEMENTED` — the `ResearchLead` → Evidence path is **staffed and demonstrated on real data**: **5 of the last 6** live MRNA runs promoted external evidence into ledger findings — every non-promotion a correct refusal — and an 8-case live negative acceptance minted exactly 1. Found and fixed **two pre-existing verification-gate defects** and four wiring defects. Both flags still default off |
| V3.11 | Production hardening and final product acceptance | `IMPLEMENTED` — 11.1-11.5 merged + 2 correctives; all four V3.10 blockers closed; live contract VERIFIED **16/16** on 2026-09-07 across **both** DeepSeek endpoints. **11.1.2 reversed 11.1.1's central finding: server-side web search DOES exist, on `/responses`.** DeepSeek is still not release-critical |

`IMPLEMENTED` = code, tests and local contract complete. `VALIDATED` = realistic
end-to-end validation performed. **No V3 phase can reach `VALIDATED` in the sense
of live-deployed use during this campaign, because deployment is not authorised.**
Where a phase has a real-document or real-database acceptance run behind it, that
is recorded explicitly rather than being allowed to pass as production validation.

## Completed slice merges

| Date | Slice | Branch | Merge into `develop/v3` |
|---|---|---|---|
| 2026-09-04 | V3 documentation baseline | `feature/v3-docs-architecture-baseline` | `4ce2336` |
| 2026-09-04 | V3.0.1 durable job contract | `feature/v3-0-1-durable-job-contract` | `61d7243` |
| 2026-09-04 | V3.0.2 worker executor | `feature/v3-0-2-worker-executor` | `473fa97` |
| 2026-09-04 | V3.0.3 company research on durable jobs | `feature/v3-0-3-company-research-on-durable-jobs` | `04d2df1` |
| 2026-09-04 | V3.0.4 server-side numeric verification | `feature/v3-0-4-server-side-numeric-verification` | `31b9fa2` |
| 2026-09-04 | V3.0.5 run consumption telemetry | `feature/v3-0-5-run-consumption-telemetry` | `ab3fda4` |
| 2026-09-04 | V3.1.1 raw artifact store | `feature/v3-1-1-raw-artifact-store` | `f427cac` |
| 2026-09-04 | V3.1.2 corpus document model | `feature/v3-1-2-research-corpus-schema` | `74ead56` |
| 2026-09-04 | V3.1.3 full parsed representation | `feature/v3-1-3-full-text-persistence` | `a3acca5` |
| 2026-09-04 | V3.1.4 search backend abstraction | `feature/v3-1-4-search-interface` | `001948a` |
| 2026-09-04 | V3.1.5 document-aware chunking | `feature/v3-1-5-document-aware-chunking` | `1e2e781` |
| 2026-09-04 | V3.1.6 corpus retrieval service | `feature/v3-1-6-corpus-retrieval-service` | `d867f70` |
| 2026-09-05 | V3.1.7 reprocessing lifecycle | `feature/v3-1-7-reprocessing-lifecycle` | `fd039bc` |
| 2026-09-05 | V3.1 phase gate | `feature/v3-1-phase-gate-report` | `a7a0a53` |
| 2026-09-05 | Campaign state | `feature/v3-campaign-state` | `a7e0776` |
| 2026-09-05 | V3.2.1 entity master | `feature/v3-2-1-entity-master` | `01f0f13` |
| 2026-09-05 | V3.2.2 company backfill | `feature/v3-2-2-company-backfill` | `4e0a90d` |
| 2026-09-05 | V3.2.3 entity resolution | `feature/v3-2-3-entity-resolution` | `f3286ad` |
| 2026-09-05 | V3.2.3.1 identifier sources | `feature/v3-2-3-1-identifier-sources` | `d34c65f` |
| 2026-09-05 | V3.2.4 relationships, scopes, segments | `feature/v3-2-4-entity-relationships` | `ad1a796` |
| 2026-09-05 | V3.2.5 universe generation | `feature/v3-2-5-universe-generation` | `df75ce6` |
| 2026-09-05 | V3.2 phase gate | `feature/v3-2-phase-gate-report` | `4f0cb0a` |
| 2026-09-05 | V3.3.1 agent tool contracts | `feature/v3-3-1-agent-tool-contracts` | `b4f31ef` |
| 2026-09-05 | V3.3.2 fact and series tools | `feature/v3-3-2-fact-and-series-tools` | `1a86871` |
| 2026-09-05 | V3.3.3 calculation engine | `feature/v3-3-3-calculation-engine` | `bc0cd15` |
| 2026-09-05 | V3.3.4 corpus search tools | `feature/v3-3-4-corpus-search-tool` | `42b8336` |
| 2026-09-05 | V3.3 phase gate | `feature/v3-3-phase-gate-report` | `7772b1f` |
| 2026-09-05 | V3.4.1 provider interfaces | `feature/v3-4-1-provider-interfaces` | `d628ae7` |
| 2026-09-05 | Decision record — ADR-047..052 | `feature/v3-decisions-resolved` | `ec8ea65` |
| 2026-09-05 | V3.4.1.1 rights-based governance | `feature/v3-4-1-1-rights-based-governance` | `66ddd30` |
| 2026-09-05 | V3.4.3 DeepSeek providers | `feature/v3-4-3-deepseek-providers` | `07c7032` |
| 2026-09-05 | V3.4.4 research lead promotion | `feature/v3-4-4-research-lead-promotion` | `5cd3910` |
| 2026-09-06 | V3.4.9 PostgreSQL search backend | `feature/v3-4-9-pgvector-search-backend` | `08cb013` |
| 2026-09-06 | V3.4.11 research-mode budgets (corrective) | `feature/v3-4-11-research-mode-budgets` | `3f8013b` |
| 2026-09-06 | V3.4.5 provider benchmark harness | `feature/v3-4-5-provider-benchmark-harness` | `8d20747` |
| 2026-09-06 | V3.4.7 macro observation store | `feature/v3-4-7-macro-observation-store` | `5b5045e` |
| 2026-09-06 | V3.4.8 transcripts and IR events | `feature/v3-4-8-transcript-provider` | `e481da6` |
| 2026-09-06 | V3.4.10 bounded issuer-site traversal | `feature/v3-4-10-issuer-site-traversal` | `ac970fb` |
| 2026-09-06 | V3.4 phase gate | `feature/v3-4-phase-gate-report` | `1348425` |
| 2026-09-06 | V3.5.1 research ledger schema | `feature/v3-5-1-research-ledger-schema` | `ed02be7` |
| 2026-09-06 | V3.5.2 research director | `feature/v3-5-2-research-director` | `ad2cd2b` |
| 2026-09-06 | V3.5.3 bounded investigation loop | `feature/v3-5-3-bounded-investigation-loop` | `6b50bb7` |
| 2026-09-06 | V3.6.1 playbook schema | `feature/v3-6-1-playbook-schema` | `e070a70` |
| 2026-09-06 | V3.6.2 five industry playbooks | `feature/v3-6-2-industry-playbooks` | `2408ca6` |
| 2026-09-06 | V3.7.1 Council V2 inputs | `feature/v3-7-1-council-v2-inputs` | `ce8c856` |
| 2026-09-06 | V3.7.2 Red Team challenge round | `feature/v3-7-2-red-team-challenge-round` | `80010ae` |
| 2026-09-06 | V3.8.1 research memory | `feature/v3-8-1-research-memory` | `4c873aa` |
| 2026-09-06 | V3.8.2 research delta | `feature/v3-8-2-research-delta` | `d306c4c` |
| 2026-09-06 | V3.9.1 monitoring | `feature/v3-9-1-monitoring` | `800f92d` |
| 2026-09-06 | **V3 release candidate report** | `feature/v3-release-candidate-report` | `96bcd1d` |
| 2026-09-06 | V3.10.1 DeepSeek live contract (opt-in) — `BLOCKED ON CREDENTIAL` | `feature/v3-10-1-deepseek-live-contract` | `e299c55` |
| 2026-09-06 | V3.10.2 real agent adapters + routing | `feature/v3-10-2-real-model-adapters` | `9e9e26d` |
| 2026-09-06 | V3.10.3 the V3 pipeline at the front door, behind a flag | `feature/v3-10-3-v3-pipeline` | `bd8b530` |
| 2026-09-06 | **V3.10.4 real-issuer acceptance** | `feature/v3-10-4-issuer-acceptance` | `86e499c` |
| 2026-09-06 | **V3.11.2 real-document scope resolution** | `feature/v3-11-2-scope-resolution` | `b63bd2c` |
| 2026-09-06 | **V3.11.1 / 11.3 / 11.4 playbook completion, DeepSeek optional, real cost** | `feature/v3-11-3-playbook-completion` | `e1b2a44` |

## Corrective slices

A corrective is a *separate* branch merged after the slice it fixes, never a
rewrite of history. Five exist, and each one is a defect a real run found:

| Date | Corrective | Branch | Merge | What it fixed |
|---|---|---|---|---|
| 2026-09-04 | V3.0.2.1 bound reclaim attempts | `feature/v3-0-2-1-bound-reclaim-attempts` | `c9e3d8d` | A killed worker never calls `fail()`, so a job that kills its worker was reclaimed forever. |
| 2026-09-04 | V3.0.3.1 research-stage accuracy | `feature/v3-0-3-1-research-stage-accuracy` | `e9b6e9c` | The stage map named the wrong stages and the two longest phases reported nothing. |
| 2026-09-05 | Untrack the data-source inventory | `fix/v3-untrack-data-source-inventory` | `2810aef` | Files committed that are [OPEN DECISION #18](OPEN_DECISIONS.md#18-fate-of-docsdata_source_inventorymd--xlsx) and the user's to place. |
| 2026-09-06 | `get_recent_filings`, because a real run could not finish without it | `fix/v3-10-3-1-recent-filings-tool` | `62f8c75` | Biotech's blocking question required a tool **nothing implemented**, so a biotech run could never convene its Council. Merged with the three integration defects the first MRNA run exposed: a `research_runs` id passed into a column FK'd to `research_jobs` — **every tool call failed to persist and killed the transaction**; `lookup_entity` called with a `company_id` when it takes a ticker; and the delta silently never computed because the prior run was re-queried after this run had opened. |
| 2026-09-06 | A finding inherits the period and scope of its evidence | `fix/v3-10-4-1-finding-period-scope` | `2c1cc27` |
| 2026-09-06 | Citations are checked to actually resolve | `fix/v3-11-5-citation-resolution-check` | `9b41bc0` |
| 2026-09-06 | **The DeepSeek contract, verified** *(its web-search finding was later REVERSED — see the next row)* | `fix/v3-11-1-1-deepseek-live-contract` | `f037f1b` | A real key made 7 of 8 live questions fail. Found: the adapter's repr **printed the key into pytest output**; `response_format` sent unconditionally so every completion 400'd; tool `parameters` needed a JSON Schema; ~~no server-side web search exists at all~~ **(WRONG — it does, on `/responses`)**; a credential silently re-routed Investigator work off Azure OpenAI; and seven tests asserted a property of the developer's machine. | "Citations resolve" was on the acceptance gate and had never been measured — the harness checked that a finding HAS citations, not that they lead anywhere. | **Findings were exempt from period and scope integrity.** Real findings said "FY2026 projected" over FY2025 actuals with `period_key=None`. Now inherited on agreement or nothing; disagreement discards the finding and opens a `conflicting_sources` gap — which then fired on live SEC data. |
| 2026-09-07 | **DeepSeek's web search exists; it was on the other endpoint** | `fix/v3-11-1-2-deepseek-responses-web-search` | `c7b2f3f` | V3.11.1.1 probed only `/chat/completions` and reported that DeepSeek has no server-side web search. It has one, on **`POST /responses`** — verified live 16/16. **An absence measured on one endpoint is not an absence.** `search()` now really searches; candidates come only from pages the provider actually opened (there are **no structured citations**), and because `max_tool_calls` and `filters.allowed_domains` are accepted-and-ignored, spend and domain limits are enforced client-side. Also closed a **second credential-leak path**: any `repr` of `Settings` printed every API key, so all five are now `Field(repr=False)`. |

| 2026-09-07 | **V3.12 external research integration** | `feature/v3-12-external-research-integration` | `a2f0f4f` | The last unstaffed role. `search_web` and `fetch_public_source` — reserved in the vocabulary since V3.3 — are implemented, gated on the external flag, and chained by the Investigator: search returns CLAIMS with no citable id, and only InvestingBuddy's own fetch through `verify_lead` may mint one. Live: **5 of the last 6** MRNA runs promoted `ev:x:` evidence into findings (the six non-promotions all correct refusals); 8 negative cases minted 1. Fixed two **pre-existing** gate defects a real document exposed — a fabricated value matching under a relative tolerance, and a period conflict that was *skipped* rather than failed — plus four wiring defects only the pipeline found. |

## Migrations

Every V3 migration is applied, rolled back and re-applied against **real
PostgreSQL 16** on a throwaway database that is then dropped, and the local dev
database is re-checked afterwards to confirm it is still at 018.

| Rev | Tables / change | Verified on | In a deployed environment |
|---|---|---|---|
| 019 | `research_jobs` | scratch PostgreSQL | **No** |
| 020 | `research_run_consumption` | scratch (`ib_v3_migcheck_020`, dropped) | **No** |
| 021 | `research_artifacts` | scratch (`ib_v3_migcheck_021`, dropped) | **No** |
| 022 | `research_documents`, `research_document_versions` | scratch (`ib_v3_migcheck_022`, dropped) | **No** |
| 023 | `research_document_derivations` / `_pages` / `_sections` / `_tables` | scratch (`ib_v3_migcheck_023`, dropped) | **No** |
| 024 | `research_document_chunks` | scratch (`ib_v3_migcheck_024`, dropped) | **No** |
| 025 | `research_document_derivations.extraction_profile` | scratch (`ib_v3_migcheck_025`, dropped) | **No** |
| 026 | `legal_entities`, `securities`, `security_listings`, `entity_identifiers`, `entity_aliases` | scratch (`ib_v3_migcheck_026`, dropped). Upgraded, downgraded, re-upgraded; ORM/DDL drift check clean; **every uniqueness and CHECK guarantee exercised with real conflicting INSERTs in PostgreSQL 16**, not only through the ORM. | **No** |
| 027 | `companies.legal_entity_id` (nullable, `SET NULL`) + index | scratch (`ib_v3_migcheck_027`, dropped). A column-by-column diff of `companies` shows **exactly one added column**; `confdeltype='n'` and a real entity DELETE left the company row in place, unlinked; downgrade restored the column list identically. | **No** |
| 028 | `entity_relationships`, `reporting_scopes`, `business_segments` + `securities.underlying_security_id` / `receipt_ratio` | scratch (`ib_v3_migcheck_028`, dropped). Exactly **two** columns added to `securities`, `companies` **identical**; **eleven** guarantees exercised with real conflicting statements in PostgreSQL; drift check clean across all nine tables including CHECK constraints. | **No** |
| 029 | `research_tool_calls` | scratch (`ib_v3_migcheck_029`, dropped). 41 → 42 tables and back; every CHECK exercised with a real statement; deleting the company an audit row refers to left **3 rows surviving, 0 still linked** — the audit record outlives what it describes. | **No** |
| 030 | `calculation_records` | scratch (`ib_v3_migcheck_030`, dropped). Eight statements exercised, including that **a refused row cannot carry a value** — a number beside a refusal is exactly what a reader takes at face value. Drift check clean. | **No** |
| 031 | `research_leads` | scratch (`ib_v3_migcheck_031`/`_031b`, dropped). All **five** CHECKs fired on real conflicting statements, including the one that matters — **a `verified` row with no hash of bytes we fetched is unstorable**. Deleting the company a lead refers to left **4 rows surviving, 0 still linked**. Drift: none. | **No** |
| 032 | `research_document_chunks`: `indexed_at`, `embedding_json`, `embedding_model`, `embedding_dim` + a GIN index over `to_tsvector('simple', text)` | scratch (`ib_v3_migcheck_032`/`_032b`, dropped). The CHECK exercised in **both** failing directions (no model, dimension zero); `EXPLAIN` confirms the planner uses the GIN index; de-indexing left **3 chunks present, 0 indexed**. Drift: none. | **No** |

| 033 | `macro_datasets`, `macro_series`, `macro_observations` | scratch (`_033`, dropped). Two *vintages* of one period both storable; two *current* readings refused by a partial unique index; a NULL value storable because a published absence is information. | **No** |
| 034 | `ir_events`, `ir_event_materials` | scratch (`_034`, dropped). `not_published` **is** storable and `available`-with-no-location is not; an `announced` event cannot claim an occurrence time. | **No** |
| 035 | The seven ledger tables | scratch (`_035`, dropped). Twelve statements; **a finding with no support is unstorable**; an unresolved disagreement is storable and an unexplained resolved one is not. | **No** |
| 036 | `research_challenges` | scratch (`_036`, dropped). **A second round is unstorable**; "resolved" with no response, and with a response but no evidence, both unstorable. | **No** |
| 037 | `research_deltas` | scratch (`_037`, dropped). An unchanged thesis beside an invalidated finding is unstorable; **the delta outlives the runs it compares** — 3 surviving, 0 still linked. | **No** |
| 038 | `watchlists`, `watchlist_entries`, `monitoring_signals` | scratch (`_038`, dropped). One **open** signal per key, enforced by a partial unique index; acknowledging lets the key be raised again. | **No** |

**The whole chain was then run end to end for the release candidate**: a fresh database
upgraded 018 → 038 (20 migrations, 23 → 61 tables), downgraded 038 → 018 (20 downgrades,
back to 23 tables), and the resulting schema compared against the pre-V3 one as a sorted
`table.column` fingerprint: **identical**. V2 columns removed or renamed: **0**. Columns
added to existing V2 tables: **1** (`companies.legal_entity_id`, nullable). ORM/DDL drift
across all 38 V3 tables: **none**.

Additive-only through V3.2 (§2.1 of the migration plan): tables, **nullable**
columns and indexes only. That is what makes `release/v2-current` code able to run
against a database with every V3 migration applied, which is what makes rollback
real rather than theoretical.

## Open user decisions

**Eleven were resolved by the user on 2026-09-05** — see
[OPEN_DECISIONS.md](OPEN_DECISIONS.md#resolution-round-2026-09-05--the-user-resolved-eleven-decisions-at-once)
and ADR-047 through ADR-052. The governing constraint is now:

> **V3 must require no new paid SaaS subscriptions or commercial data subscriptions.**

Approved and available: **Claude Code CLI** (development/orchestration only), the
**existing Azure OpenAI** infrastructure, and **DeepSeek** on pay-as-you-go.
Not purchased and not required: Exa, Perplexity, Gemini API, Anthropic API for
production, Quartr, Fiscal.ai, Browserbase, Apify, Parallel, AlphaSense.

| # | Resolution |
|---|---|
| 1 | **PostgreSQL + `pgvector`**, not Azure AI Search (ADR-047) |
| 2 | **PostgreSQL polling**; no broker required for the RC |
| 3 | Exa/Perplexity **DEFERRED**; **DeepSeek `web_search`** is the primary external search path (ADR-048) |
| 4 | DeepSeek **approved**; **rights metadata governs, not geography** (ADR-049) |
| 5 | **Existing Azure OpenAI only**, as the strong-model fallback (ADR-050) |
| 6 | Gemini Deep Research **DEFERRED / NOT ACTIVATED** |
| 7 | Claude is **not a production provider** |
| 8, 9 | **Free public issuer sources only**; missing means missing (ADR-051) |
| 11 | **Per-document rights metadata decides**; fail closed when unknown (ADR-049) |
| 13, 14 | **Bounded technical defaults per research mode**; no business budget now (ADR-052) |
| 16 | **Factual valuation context and deterministic multiples only** |

### Still open, and none of them blocks anything

| # | Decision | Why it does not block |
|---|---|---|
| [10](OPEN_DECISIONS.md#10-openfigi-usage-and-licensing) | OpenFIGI usage | FIGI is *representable* and not *obtainable*; no source populates one and a test fails if a client appears. ISIN covers the regression set. |
| [12](OPEN_DECISIONS.md#12-raw-page-and-document-retention) | Retention TTL | `V3_ARTIFACT_RETENTION_DAYS` defaults to 0 = "no TTL configured", the sweep is dry-run and scheduled by nothing. Per-document retention is now represented in policy (#11's resolution), so setting a value is configuration. |
| [15](OPEN_DECISIONS.md#15-monitoring-cadence) | Monitoring cadence | V3.9 ships the mechanism feature-gated with nothing scheduling it. |
| [17](OPEN_DECISIONS.md#17-ci-coverage-for-the-v3-branch) | CI on `develop/v3` | `scripts/v3-gates.sh` removes the manual cost; the workflow files also live on `main`. |
| [18](OPEN_DECISIONS.md#18-fate-of-docsdata_source_inventorymd--xlsx) | Data-source inventory files | Left untracked and untouched. |

**Never label an unknown cost as zero.** Still true, and now more load-bearing: the
price settings default to `0.0` meaning *unpriced*, and a benchmark that reported an
unpriced provider as free would make it look like the cheapest one. The **technical**
ceilings are now real numbers (ADR-052); the **monetary** ceiling stays unset.

## Open technical decisions

Reversible, no commercial or security commitment, resolvable by evidence — these
belong to the agent, with an ADR when material.

### Resolved

| Decision | Slice | Evidence |
|---|---|---|
| `IdentifierSource.lookup` returns `SourceLookupResult`, not `list[IdentifierClaim]` | 2.3.1 | GLEIF's `filter[entity.legalName]` is a **partial match**, so a source must be able to say "several matched and I refused to choose". A full list lets the gate accept whichever LEI is unheld — a silent misattribution of a filing history — and an empty list collapses "none" into "several". Amends an interface merged one slice earlier; blast radius was `StaticIdentifierSource` and the 2.3 tests, both moved with it. |

*(no others currently open — entries are added when a slice raises one)*

## Gate baseline

Recorded so a later run can be compared against a number rather than a memory.

| Gate | At campaign start (`35bd550`) | At the release candidate | After V3.11 | After V3.11.1.2 |
|---|---|---|---|---|
| `ruff check .` | All checks passed | All checks passed | All checks passed | All checks passed |
| `pytest tests/ -q` | 4949 passed, 12 skipped | 5870 passed, 12 skipped | **6055 passed**, 25 skipped (`ENABLE_INTEGRATION_TESTS=true`) | **6126 passed**, 39 skipped (flag unset; 6078 before V3.12) |
| `mypy app` | 71 errors in 10 files | 71 (baseline, unchanged) | 71 (baseline, unchanged) | 71 (baseline, unchanged) |
| web typecheck / lint / build | not run | **all passed** | **all passed** (re-run in V3.11.5) | typecheck + lint re-run, **passed** (web untouched) |

The 11 additional skips are the V3.10 live-provider tests, which are opt-in and skip
without a `DEEPSEEK_API_KEY`. **A skip is not a pass**, and §10.1 of the release candidate
report says so in those words.

**The pass/skip split is environment-dependent, and V3.11.1.2 had to reconcile it before
it could claim no regression.** The 6056/39 column differs from 6055/25 by +1 passed and
+14 skipped, which does *not* obviously mean "one test added". It decomposes exactly:

| Cause | passed | skipped |
|---|---|---|
| `test_v3_deepseek_providers.py` 33 → **64** collected | +31 | — |
| `test_v3_deepseek_not_release_critical.py` 21 → **25** collected | +4 | — |
| `test_v3_deepseek_live_contract.py` 14 → **16**, the 2 new ones opt-in live | — | +2 |
| `test_integration_live_providers.py` — all 12, gated on `ENABLE_INTEGRATION_TESTS`, which the V3.11 baseline run evidently had **set** and this one did not | **−12** | **+12** |
| **Total** | **+23** | **+14** |

So the recorded "6055 passed" included **12 tests making real network calls**. Compare
these numbers only alongside the value of `ENABLE_INTEGRATION_TESTS`, or a future reader
will read an environment difference as a regression — the same class of mistake as
comparing `mypy` counts across scopes.

The reconciliation that actually settles it is on **collected** counts, which are
environment-independent: the suite went 6095 → 6117 (+22), and the three changed files
went 83 → 105 (+22). Nothing else moved.

`mypy` regressed to 72 on **six** occasions during the campaign. The gate caught every one
and each was a real type error.

## Provider benchmarks

The harness is slice 4.5 and `cost_per_verified_finding` remains the primary metric —
never price alone. What the decisions of 2026-09-05 change is the *scope* of any
comparison, and that scope is stated here so no later reader mistakes an absence for a
result.

| Path | Benchmarkable | Why |
|---|---|---|
| Native InvestingBuddy retrieval | **Yes** | No credential, no spend. |
| Existing Azure OpenAI | **Yes** | Already configured in this repository. |
| DeepSeek research/search | **Yes, if credentials are provided** | Approved on pay-as-you-go. |
| Exa, Perplexity, Gemini, Anthropic | **No — by decision** | Not purchased, no credentials. |

**No cross-provider result is estimated, extrapolated or fabricated** to fill the gap.
An unavailable provider is recorded as unavailable, with the reason, which is the same
discipline the consumption module applies to an uninstrumented unit: absent is not zero.

## Known defects and risks

Carried forward, all still true:

- **`pytest tests/` is not offline on a developer machine.**
  `test_phase7_azure_openai_real.py` is `skipif`-guarded on
  `settings.llm_provider != "azure_openai"` and the local `.env` sets exactly
  that, so its 8 tests make **live Azure OpenAI calls** against the deployment's
  TPM quota. A failure in that file is a network or quota event until the file has
  been re-run on its own. **It has now happened twice in this campaign** — once in
  V3.1 (7 of 8) and once in V3.3.3 (6 of 8) — and on both occasions the file passed
  8/8 in isolation within 25 seconds and the full suite was green again immediately
  afterwards on the same commit. The protocol works; follow it before concluding
  anything about the change under test.
- **A green full suite has hidden an order-dependent failure that CI caught.** Run
  changed tests in isolation as well as in the full suite.
- **`mypy` counts are scope-dependent.** `mypy app` = 71; a broader scope
  including `tests/` = 242. Diff the *same* command; comparing across scopes
  manufactures a regression that is not there.
- **`models/__init__.py` is not exhaustive** — importing a model module directly is
  sometimes necessary for Alembic autogenerate and for tests.
- **Never `parse_scope` a raw heading.** V3.1 found the corpus labelling
  `"CONTENTS"` and `"BIG"` as business segments because a fail-closed scope parser
  was pointed at un-vetted text. Heading scope goes through the extractor's own
  vetting.
- **Two byte caps disagree.** `extract_pdf` flags `truncated` against the 8 MB
  `primary_document_max_download_bytes` while the fetch layer caps at the 35 MB
  `source_document_extraction_max_bytes`, so a 25.9 MB document is flagged
  truncated by a bound never applied to it. A pre-existing V2 inconsistency; the
  corpus under-claims completeness as a result, which is the safe direction. Its
  own slice, not yet scheduled.
- **DeepSeek's server-side web-search contract is VERIFIED** (V3.11.1.2, live
  2026-09-07) — and the way it was previously mis-measured is the lesson worth keeping:
  V3.11.1.1 probed only `/chat/completions`, found no builtin tools, and reported that the
  capability did not exist. It lives on `POST /responses`. **An absence measured on one
  endpoint is not an absence.** What the contract does *not* provide is carried in the
  code rather than assumed away: **no structured citations** (candidates come only from
  pages the provider actually opened; a URL cited only in prose is counted as
  `cited_but_never_opened` and never promoted) and **no enforced `max_tool_calls` or
  `filters.allowed_domains`** (both accepted and ignored, so spend and domain limits are
  enforced client-side and the telemetry records which side enforced them). One observed
  request made **eight** search calls and spent ~41k tokens, which is why the only bounds
  that work — `max_output_tokens` and the timeout — are both set.
  `V3_DEEPSEEK_SEARCH_ENABLED` still defaults **off**.
- **A `repr` of any settings object was a credential leak.** V3.11.1.1 fixed the
  transport's dataclass `repr`; V3.11.1.2 found the same class of leak one level up, when
  a live test failed on an assertion about an *innocent* field and pytest rendered the
  whole `Settings` object — key included — into the diff. Closed at the type via a named
  `CREDENTIAL_SETTING_FIELDS` list, every member `Field(repr=False)`. **Two lessons, both
  from review of the first attempt:** matching credentials by name suffix missed
  `database_url` (the DB password) and `staging_basic_auth`; and the behavioural test
  itself must not put the rendered object in an `assert`, or its own failure prints what
  it guards. **The validation key still needs rotating.**
- **A relative tolerance is not a search condition.** A claimed value was verified
  against a real SEC exhibit because `8675` fell within 0.5% of the document's `8650` —
  and that document holds **393 numbers**, so the window was wide enough for almost any
  invented figure. Verification now also requires the found number to round to the claim
  **at the precision the claim states**. The general lesson: a tolerance calibrated for
  *rounding* becomes a false-positive machine when it is used to *search* a large
  document.
- **A check that is skipped reads exactly like a check that passed.** `verify_lead`
  compared a claimed period only against one the platform had independently determined;
  a raw public fetch supplied none, so a claim naming `2019-Q1` sailed through against a
  2026 filing. `periods_in()` now reads what a document says about itself, and an empty
  result means *unchecked*, never "no periods".
- **A feature flag read from process-global settings cannot gate a run.**
  `implemented_tools()` ignored the `cfg` the pipeline threads through every other layer,
  so a run's own configuration could not decide its own tool surface. Same class as the
  V3.11.1.2 flag-with-no-consumer defect, one level up.
- **A test that guards a leak can BE the leak.** V3.11.1.2's security pass found
  `assert key not in repr(transport)` reading a live key — in a test that runs always,
  and that only ever fails on the day the redaction it guards has regressed. pytest
  renders both operands plus an "is contained here" expansion, so it would have printed
  the key three times. **Reduce the comparison to a bool before it reaches `assert`**;
  the AST guard now follows local bindings and is scoped to `assert` statements, and is
  proven in both directions. Same lesson as the `Settings` repr, one level further in.
- **A feature flag with no consumer is not a gate.** `V3_DEEPSEEK_SEARCH_ENABLED` was
  documented in three places as holding the search leg closed while nothing in
  application code read it — the transport's unconditional refusal had been the real
  brake, and V3.11.1.2 removed it by making search work. Enforced now in
  `DeepSeekSearchProvider.search()`. **Before trusting a flag, grep for its consumer.**
- **`(ticker, exchange)` has already resolved to the wrong issuer live** — `BA` +
  LSE returned Boeing's CIK for BAE Systems. Fixed by special-casing, not by
  identity. **Slice 2.1 makes it structurally impossible** for anything reading the
  entity master: a CIK is an identifier of a legal entity, reached only through
  listing → security → entity. It does **not** retire the old path — `companies`
  and `sec_issuer_registry` are untouched until slice 2.2 links them and a
  validated replacement exists.
- **A declared measurement must be produced.** Declaring a consumption unit
  instrumented and then not reporting it is *worse* than not declaring it, because the
  stored zero **asserts** the thing did not happen. V3.3.4 shipped exactly that, in the
  same codebase as the helper written to prevent it.
- **A row limit must bound the population the caller asked for.** V3.3.2 shipped a
  filter in Python after a `LIMIT` in SQL, which would have returned **zero** segment
  facts for a company with more Group facts than the limit. Whenever a filter and a
  bound are in different layers, the bound wins and the filter is decorative.
- **An ambiguous unit is worse than a missing one.** A missing unit makes the
  arithmetic refuse; an ambiguous one lets it run and be wrong. Two V3.2 review
  findings were exactly this — a listing currency that would have mislabelled every
  London price by 100x, and a `receipt_ratio` whose direction was underspecified and
  could have been read 16x out. Name the direction in the identifier, not in a
  comment.
- **`is_sec_eligible(None)` returns `True` by design** — for V2's legacy
  ticker-only flow, where no exchange was supplied and treating that as ineligible
  would regress every `AAPL`/`MSFT` lookup to "not sourced". That default is
  **wrong for a listing row**, where `exchange_code` NULL means the platform holds a
  listing whose venue it cannot name; inheriting it made the CIK verification rule
  fail *open* on its weakest input. Fixed in 2.3 by `_listing_is_sec_eligible`. The
  general lesson: **a shared helper's default can be right at one call site and
  wrong at another**, and "we reused the existing function" is not "we applied the
  existing rule".
- **`security_listings.quote_currency` is the quote unit, not a reporting
  currency.** LSE quotes in pence. Joining it against an issuer's reporting
  currency mislabels a London price as 100x its real value. The column is named
  for the distinction; a review pass caught the first implementation defaulting it
  from `ExchangeInfo.currency`.
- **B1 App Service headroom**: ~1.75 GB, one worker, and five concurrent analyses
  exceed the 45-minute stale threshold. Run live batches of two.

## Deferred work

| Item | Why deferred | Where it goes |
|---|---|---|
| Slice 0.6 Service Bus adapter | ADR-047 round: no broker required for the RC. Now `DEFERRED`, not `BLOCKED` — nothing is left to decide. | A later scale-out option |
| Exa / Perplexity adapters (4.2) | ADR-048: DeepSeek `web_search` is the primary path. Interface and fake retained. | A small future slice if either is approved |
| Gemini Deep Research (4.6) | `OPTIONAL / DEFERRED / NOT ACTIVATED`. | Future optional integration |
| Anthropic production adapter | Claude is not a production provider. Adapter and fake may remain. | Future optional integration |
| Quartr / Fiscal.ai adapters | ADR-051: free public issuer sources only. | Future optional integration |
| Browserbase / Apify | No paid crawler. A JS-only site is recorded **partially inaccessible**. | Browser escalation is future work |
| Reconciling the two byte caps | Pre-existing V2 inconsistency, safe direction | Its own slice |
| Wiring reprocessing onto the durable worker | Adding a job type is a slice of its own | Post-V3.3 |
| The other five `BackgroundTasks` call sites | One entry point at a time | Reviewed in the release-candidate pass |
| Removing `excerpts_json` | Not before the corpus is consumed in a validated run | Post-validation |
| A dedicated valuation phase | ADR-052 round: factual context and deterministic multiples only for this release | Separate approval |

## Next executable action

**None. The campaign is complete and awaiting user acceptance.**

Read [V3_RELEASE_CANDIDATE_REPORT.md](V3_RELEASE_CANDIDATE_REPORT.md) — §11 is the V3.11
production hardening and §12 is the recommendation, which is **`READY FOR USER ACCEPTANCE /
MAIN PROMOTION REVIEW`**.

All four V3.10 blockers are closed:

1. **DeepSeek** — the live contract is **VERIFIED across both endpoints** (16/16; model leg
   2026-09-06, search leg 2026-09-07) and DeepSeek is **still not release-critical**.
   Meeting the API found six real defects. It also produced one **wrong conclusion**, worth
   recording because the campaign shipped it for a day: V3.11.1.1 probed only
   `/chat/completions`, found no builtin tools, and reported that DeepSeek has no
   server-side web search. It has one, on `POST /responses` — V3.11.1.2 measured it.
   **An absence measured on one endpoint is not an absence** ([ADR-055](../DECISIONS.md)).
   Consequence: ADR-048 stands and **no search provider needs buying**.
   ⚠️ **The key used for validation must be rotated** — it reached terminal output through
   two distinct paths before both were closed at the type.
2. **Playbooks** — CFR, MRNA and ASML all convene. Three of the four causes were the
   platform's, not the world's.
3. **Scope** — 1.7% → 8.1% coverage on the real Richemont report with a **0.0%**
   false-positive Group rate; the €107m Specialist Watchmakers figure resolves to its
   segment.
4. **Cost** — `$0.01453` per research run, `$0.001453` per verified useful finding, priced
   from configuration with its provenance recorded.

**The one thing still not proved** is in §11.6: the `ResearchLead` → Evidence promotion path
has never run with a real external provider, because the only producer of leads is
DeepSeek. It is dormant and unit-tested. **Enabling DeepSeek later must be its own
acceptance slice.**

If acceptance is given, §9 lists the recoverable next steps, beginning with CI on
`develop/v3` (OPEN DECISION #17).

Nothing in this campaign authorises a merge to `main`, a deployment, or a migration against
the live database, and none was performed. The protected V2 refs are untouched at
`4b60e07`.
