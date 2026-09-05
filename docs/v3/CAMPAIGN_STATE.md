# InvestingBuddy V3 — Campaign State

**This file is durable campaign memory. Read it first, before any other V3
document, at the start of every session and in every delegated subtask.**

It records what is *true of the repository right now* — verified against Git and
the code, not inferred from a plan. When it disagrees with a phase-gate section in
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), the plan is the historical
record of a gate and this file is the current state; re-verify before trusting
either.

**Last verified:** 2026-09-05, at the V3.2 phase gate, by direct `git` inspection and a full local gate run.

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
| `develop/v3` HEAD | `35bd550` — 47 commits ahead of `origin/develop/v3` (`fd82d3d`), unpushed |
| Alembic head in source | **029** (`029_add_research_tool_calls`) |
| Alembic head in the deployed database | **018** — and V3 migrations 019-029 have reached **no** deployed environment |
| Alembic head in the local dev database | **018** (unchanged by V3 work; scratch databases only) |
| Current phase | **V3.3 — Research tools and calculation engine** |
| Current slice | 3.3 — `feature/v3-3-3-calculation-engine` (next) |
| Deployed | **Nothing.** `main` at `4b60e07` is the deployed product. |

Working tree at campaign start also held two untracked files —
`docs/DATA_SOURCE_INVENTORY.md` / `.xlsx`. They are
[OPEN DECISION #18](OPEN_DECISIONS.md#18-fate-of-docsdata_source_inventorymd--xlsx),
the user's call, and the campaign leaves them untracked and untouched.

## Phase status

| Phase | Goal | Status |
|---|---|---|
| V3.0 | Execution and correctness foundation | `IMPLEMENTED` |
| V3.1 | Research Corpus | `IMPLEMENTED` |
| V3.2 | Entity Master and global universe | `IMPLEMENTED` — all six slices merged; [phase gate](IMPLEMENTATION_PLAN.md#11-v32-phase-gate) |
| V3.3 | Research tools and calculation engine | `IN PROGRESS` — 3.1-3.2 merged; 3.3-3.4 open |
| V3.4 | Multi-provider runtime and source expansion | `NOT STARTED` |
| V3.5 | Research Ledger and Director | `NOT STARTED` |
| V3.6 | Industry playbooks | `NOT STARTED` |
| V3.7 | Council V2 and Red Team | `NOT STARTED` |
| V3.8 | Research Memory and Delta | `NOT STARTED` |
| V3.9 | Monitoring | `NOT STARTED` |

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
| 2026-09-05 | V3.3.2 fact and series tools | `feature/v3-3-2-fact-and-series-tools` | *(see progress log)* |

## Corrective slices

A corrective is a *separate* branch merged after the slice it fixes, never a
rewrite of history. Three exist so far, and each one is a defect a real run found:

| Date | Corrective | Branch | Merge | What it fixed |
|---|---|---|---|---|
| 2026-09-04 | V3.0.2.1 bound reclaim attempts | `feature/v3-0-2-1-bound-reclaim-attempts` | `c9e3d8d` | A killed worker never calls `fail()`, so a job that kills its worker was reclaimed forever. |
| 2026-09-04 | V3.0.3.1 research-stage accuracy | `feature/v3-0-3-1-research-stage-accuracy` | `e9b6e9c` | The stage map named the wrong stages and the two longest phases reported nothing. |
| 2026-09-05 | Untrack the data-source inventory | `fix/v3-untrack-data-source-inventory` | `2810aef` | Files committed that are [OPEN DECISION #18](OPEN_DECISIONS.md#18-fate-of-docsdata_source_inventorymd--xlsx) and the user's to place. |

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

Additive-only through V3.2 (§2.1 of the migration plan): tables, **nullable**
columns and indexes only. That is what makes `release/v2-current` code able to run
against a database with every V3 migration applied, which is what makes rollback
real rather than theoretical.

## Open user decisions

Owned by the user, and **not** to be resolved by an agent. The campaign continues
around each one and marks the dependent work `BLOCKED` rather than guessing.

| # | Decision | Blocks | Why it is the user's |
|---|---|---|---|
| [1](OPEN_DECISIONS.md#1-azure-ai-search-vs-postgresql--pgvector) | Production corpus search backend | The production backend only — the interface, fusion and in-memory reference backend are done | Cost of an additional Azure service |
| [2](OPEN_DECISIONS.md#2-service-bus-worker-topology) | Service Bus worker topology | V3.0.6 only; PostgreSQL polling is a valid production mode at this volume | Whether a second App Service is affordable |
| [3](OPEN_DECISIONS.md#3-exa-vs-perplexity-search) | Exa vs Perplexity | V3.4.2 *defaults*, not the interface | Provider spend |
| [4](OPEN_DECISIONS.md#4-deepseek-data-governance-policy) | DeepSeek data governance | V3.4.3 *enablement*, not the adapter | Legal / comfort |
| [8](OPEN_DECISIONS.md#8-transcript-provider), [9](OPEN_DECISIONS.md#9-quartr-vs-fiscalai) | Transcript provider / vendor | V3.4.8 live coverage | Commercial contract |
| [10](OPEN_DECISIONS.md#10-openfigi-usage-and-licensing) | OpenFIGI usage | Instrument-level FIGI mapping in V3.2 only | Licensing terms |
| [11](OPEN_DECISIONS.md#11-private-data-external-model-policy) | Private data to external models | Private-research *enablement* | A judgement call, not a technical one |
| [12](OPEN_DECISIONS.md#12-raw-page-and-document-retention) | Retention TTL | Nothing — the primitive ships unconfigured | Storage cost |
| [13](OPEN_DECISIONS.md#13-model-cost-thresholds), [14](OPEN_DECISIONS.md#14-research-mode-budgets) | Monetary budgets and mode budgets | Monetary *defaults*; technical ceilings exist regardless | The user's actual budget |
| [15](OPEN_DECISIONS.md#15-monitoring-cadence) | Monitoring cadence | V3.9 scheduling | Preference |
| [16](OPEN_DECISIONS.md#16-future-valuation-scope) | Valuation / fair-value scope | Out of initial V3 | Regulated-advice risk |
| [17](OPEN_DECISIONS.md#17-ci-coverage-for-the-v3-branch) | CI on `develop/v3` | Nothing — `scripts/v3-gates.sh` removes the manual cost | Touches a file that also lives on `main` |
| [18](OPEN_DECISIONS.md#18-fate-of-docsdata_source_inventorymd--xlsx) | Data-source inventory files | Nothing | Where the user wants them |

**Never label an unknown cost as zero.** `V3_ARTIFACT_RETENTION_DAYS = 0` means
"no TTL configured", the price settings default to `0.0` meaning "unpriced", and
the run ceilings default to `0` meaning "unbounded by policy, still bounded by the
technical limits". A default that reads as a business answer is a decision taken
by accident.

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

| Gate | At campaign start (`35bd550`) | After V3.3.2 |
|---|---|---|
| `ruff check .` | All checks passed | All checks passed |
| `pytest tests/ -q` | 4949 passed, 12 skipped | **5259 passed**, 12 skipped |
| `mypy app` | 71 errors in 10 files | 71 errors in 10 files (baseline; one regression to 72 was caught by the gate in 2.3 and fixed) |

## Provider benchmarks

*(none yet — V3.4.5 builds the harness; no provider default may be set without one)*

The primary metric is **cost per verified useful finding**, never price alone. A
provider claim is not a finding until InvestingBuddy has independently retrieved
its cited source and the claim has survived verification.

## Known defects and risks

Carried forward, all still true:

- **`pytest tests/` is not offline on a developer machine.**
  `test_phase7_azure_openai_real.py` is `skipif`-guarded on
  `settings.llm_provider != "azure_openai"` and the local `.env` sets exactly
  that, so its 8 tests make **live Azure OpenAI calls** against the deployment's
  TPM quota. A failure in that file is a network or quota event until the file has
  been re-run on its own.
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
- **`(ticker, exchange)` has already resolved to the wrong issuer live** — `BA` +
  LSE returned Boeing's CIK for BAE Systems. Fixed by special-casing, not by
  identity. **Slice 2.1 makes it structurally impossible** for anything reading the
  entity master: a CIK is an identifier of a legal entity, reached only through
  listing → security → entity. It does **not** retire the old path — `companies`
  and `sec_issuer_registry` are untouched until slice 2.2 links them and a
  validated replacement exists.
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
| Slice 0.6 Service Bus adapter | [OPEN DECISION #2](OPEN_DECISIONS.md#2-service-bus-worker-topology) | After the topology decision |
| Reconciling the two byte caps | Pre-existing V2 inconsistency, safe direction | Its own slice |
| Wiring reprocessing onto the durable worker | Adding a job type is a slice of its own; reprocessing is operator-invoked and a test keeps it so | Post-V3.3 |
| The other five `BackgroundTasks` call sites | One entry point at a time | Reviewed in the release-candidate pass |
| Removing `excerpts_json` | Not before V3.3 consumes the corpus in a validated run | Post-validation |
| Embeddings and an embedding provider | Provider decision in V3.4; hybrid degrades to its lexical leg, which is a worse answer and never a wrong one | V3.4 |

## Next executable action

Start **V3.3 slice 3.3** on `feature/v3-3-3-calculation-engine`: declarative
calculation definitions, typed inputs, **incompatibility refusals**, and persisted
calculation records. Migration expected.

The acceptance strategy's demonstration for V3.3 is the one that matters here: *a
calculation with incompatible periods or scopes is **refused**, not computed.* Two
primitives already decide that and must not be re-implemented:

- `ReportingPeriod.comparable_with` — fail-closed, refuses cross-type comparison, and
  refuses an unknown period against anything including another unknown.
- `FactScope` / `scope_key` — Group is not segment and unknown is not Group.

A calculation record must carry its definition, formula, inputs (by fact id), periods,
scopes, units, currency, version, result and validation, so the arithmetic is
reproducible and a wrong input is attributable. `underlying_shares_per_unit` (2.4) is
already the model for a refusal: a depositary receipt with no ratio makes per-share
arithmetic stop rather than assume 1:1.

The deterministic-arithmetic rule from the campaign brief applies directly — deterministic
code for deterministic arithmetic, never a model. And a refused calculation is a
**result**, recorded with its reason, not an exception a caller swallows.
