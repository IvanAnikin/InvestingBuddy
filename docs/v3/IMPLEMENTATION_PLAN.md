# InvestingBuddy V3 — Implementation Plan

**Status:** living document. Baseline `4b60e07` (tag `v2-final-pre-v3-2026-09-04`).
**Integration branch:** `develop/v3`. **Merge target for every slice:** `develop/v3` only.

---

## 1. Phase status

| Phase | Goal | Status |
|---|---|---|
| **V3.0** | Execution and correctness foundation | `IMPLEMENTED` — not `VALIDATED`: no live-issuer run has been performed, because V3 is not deployed. See [the phase gate](#9-v30-phase-gate). |
| **V3.1** | Research Corpus | `IMPLEMENTED` — and, unlike V3.0, with a **real-document acceptance run** behind it. Not `VALIDATED`: the run is local and the corpus is not deployed. See [the phase gate](#10-v31-phase-gate). |
| **V3.2** | Entity Master and global universe | `IMPLEMENTED` — all slices merged. See [the phase gate](#11-v32-phase-gate). |
| **V3.3** | Research tools and calculation engine | `IN PROGRESS` — slice 3.1 merged. |
| **V3.4** | Multi-provider runtime and source expansion | `NOT STARTED` |
| **V3.5** | Research Ledger and Director | `NOT STARTED` |
| **V3.6** | Industry playbooks | `NOT STARTED` |
| **V3.7** | Council V2 and Red Team | `NOT STARTED` |
| **V3.8** | Research Memory and Delta | `NOT STARTED` |
| **V3.9** | Monitoring | `NOT STARTED` |

Phase labels: `NOT STARTED` → `IN PROGRESS` → `IMPLEMENTED` → `VALIDATED` → `APPROVED`.
Only explicit user acceptance moves the release to `APPROVED FOR MAIN`.

---

## 2. Dependency order, and why it is this order

**Execution before methodology.** Every later phase makes runs longer. Building
autonomous multi-round investigation on `BackgroundTasks` means building it on
something an App Service recycle deletes. V3.0 first.

**Corpus before tools.** `search_company_corpus` is the most valuable agent tool
and it needs something to search. V3.1 before V3.3.

**Entities before global discovery.** Discovering companies you cannot uniquely
identify produces duplicate and cross-linked research. V3.2 before broad universe
expansion.

**Tools before the Director.** A Director that plans work no agent can execute is
a planning demo. V3.3 before V3.5.

**Ledger before Council V2.** Structured findings are the Council's input; without
them Council V2 is the same council with new prompts. V3.5 before V3.7.

**Memory before monitoring.** A watchlist alert is only meaningful as "this
changed relative to what we concluded". V3.8 before V3.9.

---

## 3. Slice register

Each slice is one feature branch, one focused PR, merged only into `develop/v3`.
Branch naming: `feature/v3-<phase>-<slice>-<short-name>`.

### V3.0 — Execution and correctness foundation

| Slice | Branch | Objective | Migration | Status |
|---|---|---|---|---|
| 0.1 | [`feature/v3-0-1-durable-job-contract`](slices/V3.0-1-durable-job-contract.md) | Durable job record + pure state machine (idempotency, lease, attempts, dead-letter, cancellation). No entry point changes. | **Yes** (019) | `IMPLEMENTED` |
| 0.2 | [`feature/v3-0-2-worker-executor`](slices/V3.0-2-worker-executor.md) | Broker-agnostic worker loop: claim → heartbeat → execute → complete/retry. PostgreSQL-polling mode first, no cloud dependency. | No | `IMPLEMENTED` |
| 0.2.1 | [`feature/v3-0-2-1-bound-reclaim-attempts`](slices/V3.0-2.1-bound-reclaim-attempts.md) | **Corrective.** A killed worker never calls `fail()`, so a job that kills its worker was reclaimed forever. Bound the reclaim and dead-letter it. | No | `IMPLEMENTED` |
| 0.3 | [`feature/v3-0-3-company-research-on-durable-jobs`](slices/V3.0-3-company-research-on-durable-jobs.md) | Route `/company-research/jobs` through the durable contract behind `V3_DURABLE_JOBS_ENABLED`. V2 path untouched when off. | No | `IMPLEMENTED` |
| 0.3.1 | [`feature/v3-0-3-1-research-stage-accuracy`](slices/V3.0-3.1-research-stage-accuracy.md) | **Corrective.** The stage map named the wrong stages, and the two longest phases (ingestion, council) run after the graph and reported nothing. Report the phase in flight. | No | `IMPLEMENTED` |
| 0.4 | [`feature/v3-0-4-server-side-numeric-verification`](slices/V3.0-4-server-side-numeric-verification.md) | Move canonical numeric reconciliation server-side; frontend guard stays as defence in depth. | No | `IMPLEMENTED` |
| 0.5 | [`feature/v3-0-5-run-consumption-telemetry`](slices/V3.0-5-run-consumption-telemetry.md) | Vendor-neutral consumption units + `ResearchBudget` enforcement points. | **Yes** (020) | `IMPLEMENTED` |
| 0.6 | `feature/v3-0-6-service-bus-adapter` | Optional Service Bus delivery in front of the same job store. | No | `BLOCKED` — [OPEN DECISION #2](OPEN_DECISIONS.md#2-service-bus-worker-topology), user-owned (cost). Not a V3.0 prerequisite: the recommendation is explicitly "(c) PostgreSQL polling first", which 0.2/0.3 implement. |

### V3.1 — Research Corpus

| Slice | Branch | Objective | Migration | Status |
|---|---|---|---|---|
| 1.1 | [`feature/v3-1-1-raw-artifact-store`](slices/V3.1-1-raw-artifact-store.md) | Content-addressed raw-byte retention behind an `ArtifactStore` interface; `blob_path` becomes a real retrieval path. | **Yes** (021) | `IMPLEMENTED` |
| 1.2 | [`feature/v3-1-2-research-corpus-schema`](slices/V3.1-2-research-corpus-schema.md) | `ResearchDocument` / `ResearchDocumentVersion` + the migration path from `ExtractedDocument`. | **Yes** (022) | `IMPLEMENTED` |
| 1.3 | [`feature/v3-1-3-full-text-persistence`](slices/V3.1-3-full-text-persistence.md) | Full parsed text, pages, sections and tables — not only 20 bounded excerpts, under a versioned derivation. | **Yes** (023) | `IMPLEMENTED` |
| 1.4 | [`feature/v3-1-4-search-interface`](slices/V3.1-4-search-interface.md) | `SearchBackend` protocol + `CorpusQuery`/`CorpusHit` + rank fusion + in-memory backend. **Stopped at the backend-selection gate** — [OPEN DECISION #1](OPEN_DECISIONS.md#1-azure-ai-search-vs-postgresql--pgvector). | No | `IMPLEMENTED` |
| 1.5 | [`feature/v3-1-5-document-aware-chunking`](slices/V3.1-5-document-aware-chunking.md) | Document-aware chunking with stable chunk identity + denormalized entity/period/scope filter keys. | **Yes** (024) | `IMPLEMENTED` |
| 1.6 | [`feature/v3-1-6-corpus-retrieval-service`](slices/V3.1-6-corpus-retrieval-service.md) | Typed `search_corpus(...)` + `resolve_evidence(...)`; every hit carries citation-complete lineage, and no backend query syntax reaches a caller. | No | `IMPLEMENTED` |
| 1.7 | [`feature/v3-1-7-reprocessing-lifecycle`](slices/V3.1-7-reprocessing-lifecycle.md) | Deterministic re-extraction under a new parser version or a larger budget, from the retained bytes; prior derivations retained and auditable. | **Yes** (025) | `IMPLEMENTED` |

The register above splits the original six-slice plan into seven. Slice 1.2 was
carrying both the logical document *and* every parsed sub-entity; separating them
(1.2 documents/versions, 1.3 pages/sections/tables, 1.5 chunks) keeps each one to
a single migration and a checkable acceptance criterion, which is the rule §4
states. Slice 1.7 is new and explicit: re-extraction was previously implied by
"persist raw bytes" and never given its own gate, and it is the entire reason the
bytes are retained.

### V3.2 — Entity Master and universe

| Slice | Branch | Objective | Migration | Status |
|---|---|---|---|---|
| 2.1 | [`feature/v3-2-1-entity-master`](slices/V3.2-1-entity-master.md) | `LegalEntity` / `Security` / `SecurityListing` / `EntityIdentifier` / `EntityAlias`, with identifiers **validated on write** and the uniqueness guarantees enforced by partial unique indexes. `companies` untouched. | **Yes** (026) | `IMPLEMENTED` |
| 2.2 | [`feature/v3-2-2-company-backfill`](slices/V3.2-2-company-backfill.md) | `companies.legal_entity_id` (nullable, `SET NULL`) + a resumable, idempotent, operator-invoked backfill + the `(ticker, exchange)` compatibility adapter that reports **which** identity model answered. | **Yes** (027) | `IMPLEMENTED` |
| 2.3 | [`feature/v3-2-3-entity-resolution`](slices/V3.2-3-entity-resolution.md) | Resolution with an explicit **state** — `resolved` / `ambiguous` / `conflicting` / `unresolved` — plus the identifier-claim verification gate with recorded rejection reasons. Weak (name) evidence never resolves. | No | `IMPLEMENTED` |
| 2.3.1 | [`feature/v3-2-3-1-identifier-sources`](slices/V3.2-3.1-identifier-sources.md) | Live GLEIF and SEC adapters behind `IdentifierSource`, injected providers, and `promote_entity_identity`. **Amends the protocol** so a source can *withhold* — GLEIF's name filter is a partial match. | No | `IMPLEMENTED` |
| 2.4 | [`feature/v3-2-4-entity-relationships`](slices/V3.2-4-entity-relationships.md) | `EntityRelationship` in **one canonical direction**, `ReportingScope` whose key IS `fact_scope`'s key, `BusinessSegment` as a per-period disclosure, and the ADR↔ordinary link with a ratio that makes per-share arithmetic refuse. | **Yes** (028) | `IMPLEMENTED` |
| 2.5 | [`feature/v3-2-5-universe-generation`](slices/V3.2-5-universe-generation.md) | `UniverseProvider` + a composer that de-duplicates by **listing identity** and merges only on evidence; the curated registry demoted to one provider behind a flag; the entity master as a second. | No | `IMPLEMENTED` |

### V3.3 — Research tools and calculations

| Slice | Branch | Objective | Migration | Status |
|---|---|---|---|---|
| 3.1 | [`feature/v3-3-1-agent-tool-contracts`](slices/V3.3-1-agent-tool-contracts.md) | Closed typed read-only tool vocabulary + registry that refuses a write + per-role permission and budgets checked **before** spending + `ResearchToolCall` persistence of every attempt including refusals. `lookup_entity` as the reference tool, returning the **state**. | **Yes** (029) | `IMPLEMENTED` |
| 3.2 | `feature/v3-3-2-fact-and-series-tools` | `get_financial_facts` / `_series` / `get_segment_facts`. | No | `NOT STARTED` |
| 3.3 | `feature/v3-3-3-calculation-engine` | Declarative definitions, typed inputs, incompatibility refusals, persisted records. | Yes | `NOT STARTED` |
| 3.4 | `feature/v3-3-4-corpus-search-tool` | `search_company_corpus` / `search_private_research`. | No | `NOT STARTED` |

### V3.4 — Multi-provider runtime and sources

| Slice | Branch | Objective |
|---|---|---|
| 4.1 | `feature/v3-4-1-provider-interfaces` | `ModelProvider` / `SearchProvider` / `ResearchProvider` / `BrowserProvider` + routing slots + fakes. |
| 4.2 | `feature/v3-4-2-exa-search-provider` | Exa adapter behind `SearchProvider`. Opt-in, budget-capped. |
| 4.3 | `feature/v3-4-3-deepseek-model-provider` | DeepSeek adapter behind `ModelProvider`, governance-gated to public content. |
| 4.4 | `feature/v3-4-4-research-lead-promotion` | `ResearchLead` persistence + verification gate + rejection reasons. |
| 4.5 | `feature/v3-4-5-provider-benchmark-harness` | Repeatable scored benchmark; `cost_per_verified_finding`. |
| 4.6 | `feature/v3-4-6-gemini-deep-research` | Managed Deep Research as a contractor producing leads only. |
| 4.7 | `feature/v3-4-7-macro-observation-store` | `DatasetDefinition` / `SeriesDefinition` / `Observation` + first live macro source. |
| 4.8 | `feature/v3-4-8-transcript-provider` | Transcript abstraction + first implementation. |

### V3.5-V3.9

| Slice | Branch | Objective |
|---|---|---|
| 5.1 | `feature/v3-5-1-research-ledger-schema` | Run / task / question / finding / hypothesis / gap / disagreement. |
| 5.2 | `feature/v3-5-2-research-director` | Bounded planning; playbook + prior research aware. |
| 5.3 | `feature/v3-5-3-bounded-investigation-loop` | Gap review → follow-up tasks, with every hard limit enforced and reported. |
| 6.1-6.5 | `feature/v3-6-<n>-playbook-<industry>` | Playbook schema, then luxury, biotech, semiconductors, banks, industrial/defense. |
| 7.1 | `feature/v3-7-1-council-v2-inputs` | Council consumes the ledger; findings carry stable evidence ids. |
| 7.2 | `feature/v3-7-2-red-team-challenge-round` | One bounded challenge/response round; unresolved disagreements persist to the Chair. |
| 8.1 | `feature/v3-8-1-research-memory` | Prior research state, retrievable by entity. |
| 8.2 | `feature/v3-8-2-research-delta` | What changed; which prior conclusions to revisit. |
| 9.1 | `feature/v3-9-1-monitoring` | Watchlists, new filings, event triggers, scheduled refresh, change alerts. |

---

## 4. Slice template

Every slice is documented with:

```markdown
# V3.X Slice Y — <name>

## Objective
## Why now
## Current implementation
- path:Lx-Ly
## Proposed changes
## New modules
## Existing modules reused
## DB migration        yes/no
## Infrastructure      yes/no
## Provider dependency
## Feature flags
## Security considerations
## Unit tests
## Integration tests
## Live issuer acceptance
## Acceptance criteria
1.
2.
3.
## Non-goals
## Rollback
## Dependencies
```

No "implement search" or "implement agents" branches. If a slice cannot state
three checkable acceptance criteria, it is too big.

---

## 5. Feature flags

Following the repository's existing `<area>_<name>_enabled` convention
(`app/core/config.py`), env-mapped in upper case:

```
V3_DURABLE_JOBS_ENABLED         V3_RESEARCH_PROVIDER_ENABLED
V3_CORPUS_ENABLED               V3_RESEARCH_DIRECTOR_ENABLED
V3_SEARCH_ENABLED               V3_COUNCIL_V2_ENABLED
V3_ENTITY_MASTER_ENABLED        V3_RESEARCH_MEMORY_ENABLED
```

All default **off**. Each flag's deprecation plan is recorded when it is created:
a flag exists to make a migration safe, and once the migration is validated the
flag is removed. Permanent flags are how a codebase acquires 2^n untested
configurations.

---

## 6. What V3 does not build

Autonomous trading · brokerage execution · portfolio optimization · personalized
regulated advice · 30-agent swarms · unrestricted web crawling · arbitrary shell
or code execution by research agents · a proprietary global search engine · dozens
of playbooks at once · an AlphaSense-scale licensed corpus · a broad frontend
rewrite · a speculative price-target or fair-value engine without separate
approval.

---

## 7. Risk register

| Risk | Likelihood | Impact | Mitigation | Monitoring |
|---|---|---|---|---|
| Migration breaks existing reports | Medium | High | Additive-only columns; nullable FKs; backfill before enforcement; every migration reversible and tested. | Report-render smoke over the 1,057 existing reports. |
| Worker/queue complexity | Medium | Medium | PostgreSQL-polling mode first; broker is a delivery hint, not the source of truth. | Lease-expiry and dead-letter counts. |
| Source licensing | Medium | High | `access_class` on every artifact; deny-by-default for licensed content. | Governance assertions in tests. |
| Web-search / provider cost | High | Medium | Hard retrieval caps; `cost_per_verified_finding`; benchmark before defaulting. | Per-run consumption records. |
| Search quality below expectation | Medium | High | Hybrid, never vector-only; mandatory period/scope filters; benchmark on real issuers. | `verification_survival_rate`. |
| Agent runaway loops | Medium | High | Every limit in §28 enforced *before* spending; loop reports which limit stopped it. | Round/task/call counts per run. |
| Prompt injection via fetched content | High | High | Closed read-only tool list; fetched text is data; no shell/SQL/HTTP for agents. | Tool-call audit; injection fixtures in tests. |
| Entity mis-resolution | Medium | High | Never silently merge; ambiguity is a gap. | Ambiguity-gap rate. |
| False peer selection | Medium | Medium | LLM peer lists are leads; canonical peers need a source. | Unsourced-peer count (target 0). |
| Stale research memory | Medium | Medium | New primary evidence always outranks memory; memory cites evidence ids. | `invalidated_findings` per delta. |
| Private-source leakage | Low | Critical | Per-document, per-provider `may_send_to`; deny by default; enforced at the tool boundary. | Governance test suite; provider payload assertions. |
| External provider dependency | Medium | Medium | Four independent interfaces; incumbent stays default until a challenger wins. | Benchmark cadence. |
| Test portability | Medium | Medium | Fakes only in unit tests; live tests opt-in and budget-capped. | CI runtime and network assertions. |
| Azure resource limits (B1) | High | Medium | Known: ~1.75 GB, 1 worker, 5 concurrent analyses exceed the stale threshold. Worker sizing decided before V3.0.6. | Memory and worker-timeout invariants. |
| Old-report compatibility | Medium | High | Legacy read paths kept until validated replacements exist. | Legacy-report render tests (126 of the newest 200 are legacy). |

---

## 8. Progress log

| Date | Slice | Branch | Merged to `develop/v3` |
|---|---|---|---|
| 2026-09-04 | V2 preservation + V3 line | — | tag `v2-final-pre-v3-2026-09-04`, `release/v2-current`, `develop/v3` at `4b60e07` |
| 2026-09-04 | V3 documentation baseline | `feature/v3-docs-architecture-baseline` | `4ce2336` |
| 2026-09-04 | V3.0 Slice 1 — durable job contract | `feature/v3-0-1-durable-job-contract` | `61d7243` |
| 2026-09-04 | V3.0 Slice 2 — worker executor | `feature/v3-0-2-worker-executor` | `473fa97` |
| 2026-09-04 | V3.0 Slice 2.1 — bound reclaim attempts (corrective) | `feature/v3-0-2-1-bound-reclaim-attempts` | `c9e3d8d` |
| 2026-09-04 | V3.0 Slice 3 — company research on durable jobs | `feature/v3-0-3-company-research-on-durable-jobs` | `04d2df1` |
| 2026-09-04 | V3.0 Slice 3.1 — research-stage accuracy (corrective) | `feature/v3-0-3-1-research-stage-accuracy` | `e9b6e9c` |
| 2026-09-04 | V3.0 Slice 4 — server-side numeric verification | `feature/v3-0-4-server-side-numeric-verification` | `31b9fa2` |
| 2026-09-04 | V3.0 Slice 5 — run consumption telemetry | `feature/v3-0-5-run-consumption-telemetry` | `ab3fda4` |
| 2026-09-04 | V3.1 Slice 1.1 — raw artifact store | `feature/v3-1-1-raw-artifact-store` | `f427cac` |
| 2026-09-04 | V3.1 Slice 1.2 — corpus document model | `feature/v3-1-2-research-corpus-schema` | `74ead56` |
| 2026-09-04 | V3.1 Slice 1.3 — full parsed representation | `feature/v3-1-3-full-text-persistence` | `a3acca5` |
| 2026-09-04 | V3.1 Slice 1.4 — search backend abstraction | `feature/v3-1-4-search-interface` | `001948a` |
| 2026-09-04 | V3.1 Slice 1.5 — document-aware chunking | `feature/v3-1-5-document-aware-chunking` | `1e2e781` |
| 2026-09-04 | V3.1 Slice 1.6 — corpus retrieval service | `feature/v3-1-6-corpus-retrieval-service` | `d867f70` |
| 2026-09-05 | V3.1 Slice 1.7 — reprocessing lifecycle | `feature/v3-1-7-reprocessing-lifecycle` | `fd039bc` |
| 2026-09-05 | V3.1 phase gate | `feature/v3-1-phase-gate-report` | `a7a0a53` |
| 2026-09-05 | Corrective — untrack the data-source inventory (OPEN DECISION #18) | `fix/v3-untrack-data-source-inventory` | `2810aef` |
| 2026-09-05 | Campaign state — durable campaign memory | `feature/v3-campaign-state` | `a7e0776` |
| 2026-09-05 | V3.2 Slice 2.1 — entity master | `feature/v3-2-1-entity-master` | `01f0f13` |
| 2026-09-05 | V3.2 Slice 2.2 — company backfill | `feature/v3-2-2-company-backfill` | `4e0a90d` |
| 2026-09-05 | V3.2 Slice 2.3 — entity resolution | `feature/v3-2-3-entity-resolution` | `f3286ad` |
| 2026-09-05 | V3.2 Slice 2.3.1 — identifier sources | `feature/v3-2-3-1-identifier-sources` | `d34c65f` |
| 2026-09-05 | V3.2 Slice 2.4 — relationships, scopes, segments | `feature/v3-2-4-entity-relationships` | `ad1a796` |
| 2026-09-05 | V3.2 Slice 2.5 — universe generation | `feature/v3-2-5-universe-generation` | `df75ce6` |
| 2026-09-05 | V3.2 phase gate | `feature/v3-2-phase-gate-report` | `4f0cb0a` |
| 2026-09-05 | V3.3 Slice 3.1 — agent tool contracts | `feature/v3-3-1-agent-tool-contracts` | *(this slice)* |

---

## 9. V3.0 phase gate

**Status: `IMPLEMENTED`, not `VALIDATED`.** Every slice that could be built
without a user decision is built, tested and merged. What separates this from
`VALIDATED` is live data, and that is unavailable by construction: V3 is not
deployed and its migrations must not reach the live environment.

### What the phase set out to prove (§3 of the acceptance strategy)

| Demonstration | Status |
|---|---|
| A job survives worker restart | ✅ `test_a_job_survives_a_worker_restart` — worker A killed mid-run, worker B reclaims (attempt 2) and completes |
| A duplicate submit joins rather than duplicates | ✅ `test_only_one_row_exists_after_a_duplicate_submit`, `test_concurrent_submits_produce_one_job` |
| An expired lease is reclaimed exactly once | ✅ `test_an_expired_lease_is_reclaimed_exactly_once` — 6 concurrent reclaims, 1 winner, against a real database |
| Attempts are bounded and dead-letter is reachable | ✅ `test_attempts_are_bounded_and_dead_letter_is_reachable`, and Slice 2.1 closed the hole where a *killed* worker never reached `fail()` at all |
| Cancellation is honoured at a task boundary | ✅ `test_cancellation_is_honoured_at_a_task_boundary` — step 1 completes, step 2 never starts |
| No status vocabulary drift from `research_job.py` | ✅ `TestVocabulary`; `interrupted` is still derived and never stored |

### What is deliberately NOT done

| Item | Why |
|---|---|
| Slice 0.6 — Service Bus adapter | [OPEN DECISION #2](OPEN_DECISIONS.md#2-service-bus-worker-topology), **user-owned** (whether a second App Service is affordable). Its own recommendation is "(c) PostgreSQL polling first", which 0.2/0.3 implement — so this is not a V3.0 prerequisite. |
| `develop/v3` in the CI workflows | [OPEN DECISION #17](OPEN_DECISIONS.md#17-ci-coverage-for-the-v3-branch), **user-owned**, and the files also live on `main`. `scripts/v3-gates.sh` removes the manual cost without taking the decision. |
| Budget ceilings and prices | [OPEN DECISIONS #13/#14](OPEN_DECISIONS.md#13-model-cost-thresholds), **user-owned**. #14 wants them derived from the measurements Slice 5 produces; guessing a default would answer a question asked of somebody else. |
| Live-issuer acceptance | V3 is not deployed and migration 020 has not been applied anywhere. This is the single largest gap and the reason the phase is not `VALIDATED`. |
| The other five `BackgroundTasks` call sites | Field review and market discovery still run process-local. One entry point at a time; `/company-research/jobs` is the product's front door. |

### Migrations created, not deployed

| Migration | Table | Applied where |
|---|---|---|
| 019 | `research_jobs` | Scratch PostgreSQL only. **Not** in any deployed environment. |
| 020 | `research_run_consumption` | Scratch PostgreSQL only (`ib_v3_migcheck_020`, created and dropped). **Not** in any deployed environment. The local dev database was re-checked afterwards and is still at **018**. |

### Recommended V3.1 starting slice

**1.1 — `feature/v3-1-1-raw-artifact-store`.** It is the only V3.1 slice with no
open decision in front of it: [#1](OPEN_DECISIONS.md#1-azure-ai-search-vs-postgresql--pgvector)
blocks 1.4 and [#12](OPEN_DECISIONS.md#12-raw-page-and-document-retention) shapes
1.1's *retention*, not its existence — the recommendation there ((b), bytes with
a TTL, text indefinitely) only sets the TTL value. Content-hash-addressed blob
storage plus wiring `ExtractedDocument.blob_path` as a real retrieval path is
also the prerequisite that makes re-extraction possible when a parser improves,
which the repository's own history says happens repeatedly.

---

## 10. V3.1 phase gate

**Status: `IMPLEMENTED`, not `VALIDATED`.** All seven slices are built, tested and
merged into `develop/v3`. Unlike V3.0, this phase has a **real-document acceptance
run** behind it — a genuine 25.9 MB, 169-page Pandora Annual Report 2025, fetched
through the repository's own guarded fetcher and pushed through the whole corpus.
What separates it from `VALIDATED` is that the run is *local*: the corpus is not
deployed, its migrations have reached no deployed environment, and no live
research run has consumed it.

### What the phase set out to prove (§3 of the acceptance strategy)

> *A real annual report ingests to pages/sections/chunks; a query months later
> returns the right page with citable lineage; period and scope filters actually
> constrain results; `DocumentTable` survives a borderless five-year summary.*

| Demonstration | Status |
|---|---|
| A real annual report ingests to pages/sections/chunks | ✅ On the real PNDORA document: **169 of 169 pages, 471,780 characters, 72 sections, 177 tables, 602 chunks** after a deep reprocess (40 pages / 108,000 chars / 113 chunks on the live path). Against **19,232 characters** the V2 excerpt model would have kept — **24.5×**. |
| A query months later returns the right page with citable lineage | ✅ `test_a_query_months_later_returns_the_right_page`; and on the real document, `"cash flow from operations"` → **p. 138** and `"segment information"` → **p. 110, "NOTE 2.1 SEGMENT AND REVENUE INFORMATION"**, each with a full citation label. |
| A recorded evidence id still resolves after a reindex and a reprocess | ✅ `test_an_id_survives_a_reindex`; and the real run reports *"the citation recorded BEFORE reprocessing still resolves: True"*. |
| Period and scope filters actually constrain results | ✅ `test_group_and_fy2025_together_are_expressible` narrows five chunks to one; `test_the_semantic_leg_is_still_filtered` shows a semantically perfect wrong-period match excluded; a semantic-only query is refused outright. |
| `DocumentTable` survives a borderless five-year summary | ✅ On the real document: `p14:m0` came back as a **grid** with `['2025','2024','2023','2022','2021']` and `['Revenue','32,549','31,680','28,136','26,463','23,394']`. 46 of 177 tables carry a column→period map after the deep parse. |
| Raw bytes make re-extraction possible without a re-fetch | ✅ Bytes recovered **by content hash alone**, re-parse identical in pages, sections and text; the deep reprocess made no network call. |

### What is deliberately NOT done

| Item | Why |
|---|---|
| The production search backend | [OPEN DECISION #1](OPEN_DECISIONS.md#1-azure-ai-search-vs-postgresql--pgvector), **user-owned** on cost. Both options cover the lexical layer, so a `tsvector` adapter would choose (b) in everything but name. Slice 1.4 ships the interface, the fusion and a complete in-memory reference backend; `test_the_production_backend_decision_is_not_taken_here` fails if anybody adds an adapter. |
| A retention TTL | [OPEN DECISION #12](OPEN_DECISIONS.md#12-raw-page-and-document-retention), **user-owned**. `V3_ARTIFACT_RETENTION_DAYS` defaults to 0 — "no TTL configured", never "keep forever" — and the sweep is explicit, dry-run by default, and scheduled by nothing. |
| Embeddings and an embedding provider | `CorpusChunk.embedding` is the slot; who fills it is a provider decision in V3.4. Hybrid search works today and degrades to its lexical leg, which is a worse answer and never a wrong one. |
| Reconciling the two byte caps | `extract_pdf` flags `truncated` against `primary_document_max_download_bytes` (8 MB) while the fetch layer caps at `source_document_extraction_max_bytes` (35 MB), so a 25.9 MB document is flagged truncated by a bound never applied to it. A **pre-existing V2 inconsistency**; the corpus under-claims completeness as a result, which is the safe direction. Its own slice. |
| Wiring reprocessing onto the durable worker | V3.0's job store would host it, but adding a job type is a slice of its own. Reprocessing is operator-invoked, and a test keeps it that way. |
| Legal-entity linkage | The corpus links to `companies` exactly as every other table does. V3.2's backfill gives `companies` its entity link in one place rather than this schema acquiring a second one. |
| Live-issuer acceptance against a deployed environment | V3 is not deployed and migrations 021-025 have reached nothing. This is the same gap V3.0 has, and the reason the phase is not `VALIDATED`. |

### Defects that only a real document found

Both were invisible to fixtures, and both are the pattern the acceptance strategy
predicts:

1. **Raw headings were being labelled business segments.** Section scope came from
   `fact_scope.parse_scope` applied to the heading, whose fail-closed rule —
   *anything non-empty that is not Group vocabulary is a named segment* — is right
   for a string the extractor has already vetted and wrong for a heading off a
   cover page. The real report produced `segment / "CONTENTS"`,
   `segment / "BIG"`, `segment / "PICTURE"`, and, in the other direction,
   `segment / "Group results"` — a consolidated section filed under a segment
   scope. Fixed in Slice 1.3 by routing through the extractor's own heading-scope
   vetting; both directions are now pinned by test.
2. **A deep reprocess collided on chunk ids.** A deep parse re-reads the same
   leading pages, so its first chunks shared an ordinal and an offset range with
   the live parse's and hashed to the same id. Fixed in Slice 1.7 by making the
   extraction profile part of the chunk identity — which is also the semantically
   correct answer.

### Migrations created, not deployed

| Migration | Tables | Applied where |
|---|---|---|
| 021 | `research_artifacts` | Scratch PostgreSQL only (`ib_v3_migcheck_021`, created and dropped). |
| 022 | `research_documents`, `research_document_versions` | Scratch only (`ib_v3_migcheck_022`). |
| 023 | `research_document_derivations` / `_pages` / `_sections` / `_tables` | Scratch only (`ib_v3_migcheck_023`). |
| 024 | `research_document_chunks` | Scratch only (`ib_v3_migcheck_024`). |
| 025 | `research_document_derivations.extraction_profile` | Scratch only (`ib_v3_migcheck_025`). |

Every one was applied, rolled back and re-applied against real PostgreSQL 16 on a
throwaway database that was then dropped. **The local dev database was re-checked
after each and is still at 018.** No V3 migration has reached any deployed
environment.

### Gate results at the end of the phase

```
ruff check .          All checks passed!
pytest tests/ -q      4949 passed, 12 skipped   (4647 at the start of V3.1, +302)
mypy app              Found 71 errors in 10 files   (baseline, unchanged)
npm run typecheck     PASS      (no frontend change in V3.1)
npm run lint          PASS
npm run build         PASS
```

**One gate run in the phase was not green, and it is worth recording why.** A
`scripts/v3-gates.sh` run reported `7 failed, 4942 passed`, all seven in
`test_phase7_azure_openai_real.py`. That file is `skipif`-guarded on
`settings.llm_provider != "azure_openai"` — silent in CI — and the local `.env`
enables it, so its 8 tests make **live Azure OpenAI calls** against the deployment's
TPM quota. It passed 8/8 in isolation 25 seconds later and the full suite was green
again immediately afterwards, on the same commit. V3.1 touches nothing in its
import graph. The lesson is recorded in the acceptance strategy: on a developer
machine `pytest tests/` is not offline, and a failure in that file is a network or
quota event until the file has been re-run on its own.

### Recommended V3.2 starting slice

**2.1 — `feature/v3-2-1-entity-master`.** It is the only V3.2 slice with no open
decision in front of it ([#10](OPEN_DECISIONS.md#10-openfigi-usage-and-licensing)
touches instrument identifiers, not legal-entity identity, and GLEIF is already a
live source), and it is now the binding constraint on the corpus rather than a
parallel concern: every corpus document hangs off `companies.id`, which is keyed
`UNIQUE (ticker, exchange)` and has already resolved `BA` + LSE to the wrong
issuer live. A corpus that cannot say which legal entity a document belongs to
will happily return one issuer's annual report under another's ticker, and the
scope filters that make retrieval safe are worth much less without entity identity
underneath them.

---

## 11. V3.2 phase gate

**Status: `IMPLEMENTED`, not `VALIDATED`.** All six slices are built, tested and
merged. Like V3.0 and unlike V3.1, what separates it from `VALIDATED` is live data:
migrations 026-028 have reached no deployed environment, and no live research run has
resolved an entity. Unlike V3.0, this phase has something stronger than fixtures
behind its central claims — **every schema guarantee was exercised against real
PostgreSQL 16 with real conflicting statements**, not through the ORM.

### What the phase set out to prove (§3 of the acceptance strategy)

> *Two listings resolve to one `LegalEntity`; an ambiguous match raises a gap instead
> of merging; every existing `companies` row backfills; every existing report still
> renders.*

| Demonstration | Status |
|---|---|
| Two listings resolve to one `LegalEntity` | ✅ `test_two_listings_of_one_security_resolve_to_one_entity`, and at universe scale `test_two_listings_of_one_resolved_entity_become_one_member`. Cross-listing from either venue returns one entity. |
| An ambiguous match raises a state instead of merging | ✅ `resolve(...)` returns `ambiguous` for a name-only match **even with exactly one candidate**, and `conflicting` when a LEI and a ticker disagree. `NON_ACTIONABLE_RESOLUTION_STATES` makes "do not act on this" a membership test. At backfill scale, two `companies` rows deriving one entity key with different names leave the second **unlinked**. |
| Every existing `companies` row backfills | ✅ `backfill_entities_from_companies` — resumable, idempotent, bounded, **called by nothing**. Every row gets an entity, a security and a listing; a second run changes nothing. |
| Every existing report still renders | ✅ `test_a_report_still_resolves_through_company_id_after_the_backfill`; 027 adds **one nullable column** to `companies` and nothing else, verified by a column-by-column diff before and after. |
| *(added)* The `BA`+LSE failure is structurally impossible | ✅ Two live issuers cannot share a ticker on one venue — refused by `ix_security_listings_current_venue_ticker` in PostgreSQL. A CIK is an identifier **of a legal entity**, and a CIK claimed for a non-SEC-eligible venue is refused by the verification gate whatever produced it. |

### The nine things a review pass caught that the build pass did not

Recorded because the pattern is more useful than the individual fixes: every one is a
place where working, green code was **quietly wrong about a unit, a direction or a
default**.

| # | Slice | Defect |
|---|---|---|
| 1 | 2.1 | The listing currency defaulted from the venue's *reporting* currency. LSE quotes in pence, so every London price would have been mislabelled by **100x**. Now `quote_currency`, from `price_quote_currency_for_exchange`. |
| 2 | 2.1 | `upsert_security` overwrote `security_type`, so a caller relying on the default would silently downgrade an `adr` to an ordinary share. |
| 3 | 2.1 | `delisted` was accepted on an open window — a row resolving as *current* while reading as delisted. |
| 4 | 2.1 | A comment claimed the opposite of the code beneath it. |
| 5 | 2.2 | The backfill looked up entities by the **derived key only**, so an entity already known under a stronger `lei:` key was missed, a duplicate was created, and `upsert_listing` then raised — aborting the batch and leaving a stray entity. |
| 6 | 2.2 | The same lookup could **re-open a listing somebody had deliberately closed**. |
| 7 | 2.3 | **`is_sec_eligible(None)` returns `True` by design** — correct for V2's ticker-only flow, and a **fail-open** here, where `exchange_code` NULL means a listing whose venue cannot be named. Inherited unchanged, an unnamed venue would have read as SEC-eligible and a derived CIK accepted: the Boeing bug, through the one gate built to stop it. |
| 8 | 2.4 | **`receipt_ratio`'s direction was underspecified and the test contradicted the docstring.** A bare "ratio" of 4 could mean four receipts per share or four shares per receipt; a reader who guesses wrong is out by **16x**. |
| 9 | 2.5 | The universe cap was checked **before** the merge, so a full universe threw away free information and reported an exclusion for a company that was present. |

Two general lessons, both now in the campaign state:

- **A shared helper's default can be right at one call site and wrong at another.**
  "We reused the existing function" is not "we applied the existing rule" (#7).
- **An ambiguous unit is worse than a missing one**, because a missing one makes the
  arithmetic refuse (#1, #8).

A third recurred three times and now has a tool: a **substring scan for a name fires
on the docstring explaining the rule it enforces**, and a test that fails when
somebody documents its own purpose gets deleted. `tests/helpers/source_scan.py`
checks identifiers in executable position instead, and it also *tightened* V3.1's
`test_nothing_calls_the_backfill_automatically` rather than weakening it.

### One architecture amendment, with its evidence

`IdentifierSource.lookup` returns `SourceLookupResult` rather than
`list[IdentifierClaim]` — an interface changed one slice after it merged.

GLEIF's `filter[entity.legalName]` is a **partial match**: searching "Pandora"
returns every LEI record whose legal name contains it. A source that can only return
a list has two options and both are wrong. Return every candidate, and
`verify_identifier_claim` accepts the first LEI that happens to be unheld — the gate
detects a LEI *already held by another subject* and cannot detect one belonging to a
company nobody has ingested, so that is a silent misattribution of an entire filing
history. Return nothing, and the caller cannot tell "no such entity" from "several,
and I refused to choose".

So a lookup returns claims **and** withheld findings with reasons. Withheld is not
absent: "six companies contain this name" is real information, and an empty list
throws it away. Blast radius was `StaticIdentifierSource` and the 2.3 tests, both
moved with it.

### What is deliberately NOT done

| Item | Why |
|---|---|
| OpenFIGI | [OPEN DECISION #10](OPEN_DECISIONS.md#10-openfigi-usage-and-licensing), **user-owned**. FIGI is *representable* and not *obtainable*: the scheme validates structurally, its check digit is honestly **not** validated because the variant could not be confirmed against a published vector, and `test_no_openfigi_client_exists_yet` fails if a client appears. |
| CUSIP | Licensed data from CUSIP Global Services. Adding the scheme is a governance decision, and ISIN covers the same need for every issuer in the regression set. |
| ISIN sourcing | Needs an issuer document rather than a registry. The scheme and its Luhn check are done; nothing populates it. |
| Merging two existing entities | Needs a provenance-preserving merge record, and merging is the single most dangerous operation in this schema. Not in V3.2. |
| Fuzzy or phonetic name matching | An approximate name match is a weaker version of evidence that is already too weak to act on. Adding a similarity threshold only moves the arbitrary decision into a number. |
| Enforcing `companies.legal_entity_id` NOT NULL | Step 3 of the three-step pattern, and it must not happen: NULL is a **permanent** state for a row the backfill refused to link. |
| Wiring the universe composer into the discovery endpoint | A behaviour change on a live path. The flag exists so it can be validated first. |
| Populating relationships from a source | Needs a filing-structure parser. The schema, the canonical direction and the refusals are done. |
| Live-issuer acceptance | V3 is not deployed and 026-028 have reached nothing. The same gap V3.0 and V3.1 have. |

### Migrations created, not deployed

| Migration | Tables / change | Applied where |
|---|---|---|
| 026 | `legal_entities`, `securities`, `security_listings`, `entity_identifiers`, `entity_aliases` | Scratch PostgreSQL only (`ib_v3_migcheck_026`, dropped). |
| 027 | `companies.legal_entity_id` (nullable, `SET NULL`) + index | Scratch only (`ib_v3_migcheck_027`, dropped). |
| 028 | `entity_relationships`, `reporting_scopes`, `business_segments`, + `securities.underlying_security_id` / `receipt_ratio` | Scratch only (`ib_v3_migcheck_028`, dropped). |

Each was applied, rolled back and re-applied against real PostgreSQL 16 on a
throwaway database that was then dropped. **The local dev database was re-checked
after each and is still at 018, with none of the new tables and no
`legal_entity_id` column.** An ORM-versus-DDL drift check across all nine entity and
company tables — columns, nullability, indexes, unique constraints, FKs and CHECK
constraints — reported `DRIFT: none`.

Additive-only throughout, per §2.1 of the migration plan: three migrations, eight new
tables, **three** nullable columns, nothing altered. `companies` was diffed
column-by-column before and after 027 and 028 and is otherwise byte-identical, which
is what makes "`release/v2-current` runs unchanged against a migrated database" a
checked property rather than a claim.

### Guarantees exercised against real PostgreSQL, not through the ORM

Twenty statements, because "the database enforces it" is otherwise a claim about
SQLAlchemy:

| Attempt | Result |
|---|---|
| `BA` on `XLON` **and** `XNYS` | allowed — the point |
| `BA` twice on `XLON` for a different issuer | refused, `ix_security_listings_current_venue_ticker` |
| reusing `BA` on `XLON` after the window closed | allowed |
| the same LEI on a second legal entity | refused, `ix_entity_identifiers_current_value` |
| an identifier with no subject / both subjects | refused, `ck_entity_identifiers_exactly_one_subject` |
| `confidence = 1.5` | refused, `ck_entity_identifiers_confidence_range` |
| a second primary security for one entity | refused, `ix_securities_one_primary` |
| deleting a legal entity | securities, listings and identifiers cascade; **`companies` untouched** |
| deleting a linked entity | the company survives with `legal_entity_id` NULL (`confdeltype='n'`) |
| a duplicate live `(subject, object, type)` | refused, `ix_entity_relationships_current` |
| a self-relationship | refused, `ck_entity_relationships_no_self_reference` |
| Group **and** a segment scope on one entity | both allowed — the CFR shape |
| the same `scope_key` twice for one entity | refused, `ix_reporting_scopes_entity_key` |
| a predecessor scope with **no** `rename_source` | refused, `ck_reporting_scopes_rename_needs_a_source` |
| two periods of one scope / the same period twice | allowed / refused |
| `receipt_ratio = 0` | refused, `ck_securities_receipt_ratio_positive` |
| deleting an underlying security | the receipt survives, `underlying_security_id` NULL |

### Gate results at the end of the phase

```
ruff check .          All checks passed!
pytest tests/ -q      5174 passed, 12 skipped   (4949 at the start of V3.2, +225)
mypy app              Found 71 errors in 10 files   (baseline, unchanged)
```

One `mypy` regression occurred during the phase — 71 → 72, in slice 2.3 — and the
gate caught it. Fixing it surfaced review finding #7 above, which is the strongest
argument in this phase for keeping a gate that is normally green: the type error was
trivial and the fail-open it was sitting next to was not.

No web gate: V3.2 changes no frontend file.

### Recommended V3.3 starting slice

**3.1 — `feature/v3-3-1-agent-tool-contracts`.** It is the only V3.3 slice with
nothing in front of it, and it is the binding constraint on every later phase: the
Research Director (V3.5) plans work that tools execute, and a Director planning work
no agent can perform is a planning demo.

Three things beneath it are now available and should be used rather than
re-established. `lookup_entity` is `entities.resolution.resolve` and must return the
**state**, not a best match — a tool that hands an agent an `ambiguous` result as if
it were resolved undoes the whole of V3.2. `search_company_corpus` is
`corpus.retrieval.search_corpus`, whose period and scope filters are already
mandatory. And `ResearchToolCall` persistence should record consumption in the units
V3.0.5 already defined, rather than inventing a second vocabulary for the same
counters.

The security boundary is the slice's real content: a closed, typed, read-only tool
list, per-role budgets enforced **before** spending, and no raw SQL, shell,
filesystem or unrestricted HTTP for any agent. Fetched content is data, never
instructions.
