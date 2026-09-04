# InvestingBuddy V3 — Implementation Plan

**Status:** living document. Baseline `4b60e07` (tag `v2-final-pre-v3-2026-09-04`).
**Integration branch:** `develop/v3`. **Merge target for every slice:** `develop/v3` only.

---

## 1. Phase status

| Phase | Goal | Status |
|---|---|---|
| **V3.0** | Execution and correctness foundation | `IN PROGRESS` |
| **V3.1** | Research Corpus | `NOT STARTED` |
| **V3.2** | Entity Master and global universe | `NOT STARTED` |
| **V3.3** | Research tools and calculation engine | `NOT STARTED` |
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
| 0.2 | `feature/v3-0-2-worker-executor` | Broker-agnostic worker loop: claim → heartbeat → execute → complete/retry. PostgreSQL-polling mode first, no cloud dependency. | No | `NOT STARTED` |
| 0.3 | `feature/v3-0-3-company-research-on-durable-jobs` | Route `/company-research/jobs` through the durable contract behind `V3_DURABLE_JOBS_ENABLED`. V2 path untouched when off. | No | `NOT STARTED` |
| 0.4 | `feature/v3-0-4-server-side-numeric-verification` | Move canonical numeric reconciliation server-side; frontend guard stays as defence in depth. | No | `NOT STARTED` |
| 0.5 | `feature/v3-0-5-run-consumption-telemetry` | Vendor-neutral consumption units + `ResearchBudget` enforcement points. | Yes | `NOT STARTED` |
| 0.6 | `feature/v3-0-6-service-bus-adapter` | Optional Service Bus delivery in front of the same job store. | No | `NOT STARTED` |

### V3.1 — Research Corpus

| Slice | Branch | Objective | Migration |
|---|---|---|---|
| 1.1 | `feature/v3-1-1-raw-artifact-store` | Persist raw bytes to Blob with content-hash addressing; wire `blob_path` as a real retrieval path. | Yes |
| 1.2 | `feature/v3-1-2-research-corpus-schema` | `ResearchDocument` / `Version` / `Page` / `Section` / `Chunk` / `Table`. | Yes |
| 1.3 | `feature/v3-1-3-full-text-persistence` | Persist full parsed text, not only 20 bounded excerpts. Backfill path for existing `ExtractedDocument` rows. | No |
| 1.4 | `feature/v3-1-4-search-interface` | `SearchBackend` protocol + PostgreSQL lexical implementation. No vendor SDK in domain code. | Yes |
| 1.5 | `feature/v3-1-5-hybrid-search` | Embeddings + hybrid ranking, with mandatory entity/period/scope filters. | Yes |
| 1.6 | `feature/v3-1-6-citation-preserving-retrieval` | Retrieval results carry full lineage; stable evidence identifiers replace run-local `E1`/`E2`. | Yes |

### V3.2 — Entity Master and universe

| Slice | Branch | Objective | Migration |
|---|---|---|---|
| 2.1 | `feature/v3-2-1-entity-master` | `LegalEntity` / `Security` / `SecurityListing` / `Identifier`. | Yes |
| 2.2 | `feature/v3-2-2-company-backfill` | Backfill every `companies` row; `companies.legal_entity_id` FK. Zero broken reports. | Yes |
| 2.3 | `feature/v3-2-3-entity-resolution` | Resolution with explicit ambiguity — never a silent merge. | No |
| 2.4 | `feature/v3-2-4-entity-relationships` | `EntityRelationship`, `ReportingScope`, `BusinessSegment`. | Yes |
| 2.5 | `feature/v3-2-5-universe-generation` | Universe from identifier sources; curated registry demoted to one source behind a flag. | No |

### V3.3 — Research tools and calculations

| Slice | Branch | Objective | Migration |
|---|---|---|---|
| 3.1 | `feature/v3-3-1-agent-tool-contracts` | Typed read-only tool interface + registry + per-role budgets + `ResearchToolCall` persistence. | Yes |
| 3.2 | `feature/v3-3-2-fact-and-series-tools` | `get_financial_facts` / `_series` / `get_segment_facts`. | No |
| 3.3 | `feature/v3-3-3-calculation-engine` | Declarative definitions, typed inputs, incompatibility refusals, persisted records. | Yes |
| 3.4 | `feature/v3-3-4-corpus-search-tool` | `search_company_corpus` / `search_private_research`. | No |

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
| 2026-09-04 | V3.0 Slice 1 — durable job contract | `feature/v3-0-1-durable-job-contract` | pending |
