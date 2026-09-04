# InvestingBuddy V3 — Architecture Specification

**Status:** V3 TARGET unless a section is marked `CURRENT`.
**Baseline inspected:** `4b60e07` (tag `v2-final-pre-v3-2026-09-04`), 2026-09-04.
**Source-of-truth order:** repository code → migrations → tests → infra → this document.

---

## 1. What V3 is

> InvestingBuddy V3 is an evidence-first autonomous investment-research operating
> system in which specialist research agents can plan investigations, use
> public/private/structured research tools, search and retrieve documents,
> perform deterministic financial analysis, identify evidence gaps, challenge
> conclusions, and build persistent institutional research memory before an
> Investment Council synthesizes the result for a human investor.

V2 already does the hard, unglamorous half of this: it fetches primary issuer
documents, extracts period- and scope-tagged financial facts, refuses to
reconcile numbers it cannot ground, runs an LLM council over a bounded evidence
pack, and puts a human in front of every publication. What it does **not** do is
*investigate*. A V2 run assembles one frozen evidence pack up front and hands it
to the council; if the pack is missing the thing the analysis needs, the council
can only say so. It cannot go and look.

V3's central change is therefore not "more agents". It is:

1. **Durable execution**, so research can take as long as research takes.
2. **A persistent corpus**, so a document read once is searchable forever.
3. **Read-only agent tools**, so an analyst agent can pull evidence mid-run.
4. **A bounded investigation loop**, so a gap becomes a follow-up task instead of
   a caveat in the final report.
5. **A provider-neutral runtime**, so the cheapest adequate model does the bulk
   work and the strongest model does synthesis — and neither vendor owns the
   research domain.

---

## 2. Where V2 stands today (`CURRENT`)

This section is derived from the code at `4b60e07`, not from older documents.

### 2.1 What is genuinely strong and must be preserved

| Capability | Where | Why it is load-bearing |
|---|---|---|
| Job lifecycle rules in one module | `apps/api/app/services/research_job.py:59-77`, `:227-303` | States, staleness and the *derived* `interrupted` status live in one place, so two entry points cannot disagree about whether a job is alive. `interrupted` is computed at read time rather than stored — deliberately, because a stored status needs a writer that is running, which is exactly what is missing in the case it describes. |
| SSRF-safe issuer fetching | `apps/api/app/services/sources/safe_web_fetcher.py:1-31` | HTTPS-only, per-issuer host allowlist, guarded redirects, byte caps, never raises. Not a general web fetcher — by design. |
| DNS-rebinding-safe transport | `apps/api/app/services/sources/pinned_transport.py` | Resolve-then-connect IP pinning closes the TOCTOU that an allowlist alone leaves open. |
| Period semantics | `apps/api/app/services/sources/financial_period.py` | Annual ≠ interim ≠ quarter; split years handled; unknown stays unknown. |
| Scope semantics | `apps/api/app/services/sources/fact_scope.py`, persisted at `apps/api/app/models/extracted_document.py:181-183` | Group vs segment is a **typed persisted column**, not an in-memory string (ADR-031). |
| Extraction pipeline versioning | `apps/api/app/services/sources/extraction_pipeline_version.py:305-306` | Facts carry the parser version that produced them; a stale row is never assumed compatible with current code. Currently version 15. |
| Fact validation + lifecycle | `extracted_fact_validator.py`, `extracted_document.py:155-168` | `validation_status`, `needs_human_review`, `is_active` — a superseded fact is deactivated, not deleted. |
| Deterministic source registry | `apps/api/app/services/sources/registry.py:1-19` | Policy and identity per source; "planned" surfaces as a declared gap, never silent absence. Health checks are network-free. |
| Provider-pluggable LLM client | `apps/api/app/services/llm/client.py:149-361` | Abstract `LLMClient` with `complete_json`, typed transient/permanent error taxonomy, token-usage accounting, and a factory that returns `None` (keeping the deterministic path) rather than crashing. |
| Fail-closed report assembly | `final_report_generator.py`, `report_consistency.py`, `citation_checker.py` | Contradictions become assertions; a missing number stays missing. |

### 2.2 The structural limits V3 exists to remove

| Limit | Evidence in code | Consequence |
|---|---|---|
| **Execution is process-local.** Job *state* is durable in PostgreSQL, but the work runs in the API process via FastAPI `BackgroundTasks`. | `company_research.py:141`, `field_review.py:128`, `market_discovery.py:117,225,456,582`; the honest admission is in `research_job.py:36-45` | An App Service recycle kills every in-flight run. Recovery is "re-run", not "resume". With `DEPLOYED_GUNICORN_WORKERS = 1` (`config.py:35`) a restart provably kills everything, which is why orphan detection can be instant. |
| **No research corpus.** `ExtractedDocument` persists *bounded excerpts* (`excerpts_json`) and validated facts, plus a nullable `blob_path` that is not the retrieval path. | `apps/api/app/models/extracted_document.py:62-96` | A 169-page annual report is read, 20 excerpts are kept, and the rest is discarded. Three months later nothing can be asked of that document. |
| **Evidence is frozen before reasoning starts.** The council receives one pre-built pack. | `apps/api/app/services/llm/evidence_pack.py` (1,047 lines of packing), `council.py:704-731` | The council can only report a gap; it cannot close one. |
| **Issuer identity is `(ticker, exchange)`.** | `apps/api/app/models/company.py:46` — `UniqueConstraint("ticker","exchange")` | Cross-listings, ADRs, ticker changes and parent/subsidiary structure are unrepresentable. A known live failure mode: ticker/exchange collisions (`BA` + LSE → BAE vs Boeing). |
| **The discovery universe is curated.** | `discovery_seed_universe` (`config.py:389`), `market_universe_builder.py`, `verified_issuer_sources.py` (13-issuer allowlist) | A company outside the registry cannot be discovered. |
| **Most sources are reference-only.** 39 registry sources, **8** live-fetch; 28 emit a source *pointer* plus an honest gap. | `docs/DATA_SOURCE_INVENTORY.md` | Macro, commodity and event data are named, not queried. |
| **Arithmetic happens in prose.** No calculation record with typed inputs. | `report_consistency.py` reconciles *after the fact* | Derived metrics cannot be re-verified from their inputs. |
| **Evidence identity is run-local.** Citations reference positional `E1`/`E2` handles within one run. | `citation_checker.py`, `discovery_citation_checker.py` | Evidence cannot be referenced across runs, so research memory has nothing stable to point at. |
| **Every run starts from zero.** | no prior-research read path in `company_research_service.execute_company_research:152` | A refresh cannot say what changed. |

### 2.3 Pre-V3 blockers, re-verified at `4b60e07`

The prompt's §50 lists candidates from an earlier audit. Verified status today:

| Candidate blocker | Status at `4b60e07` |
|---|---|
| Synchronous PDF parsing on the API event loop | **RESOLVED.** PR #188 moved blocking extraction off the loop (`live_fetchers.py:448` `asyncio.to_thread`) and raised the deployed gunicorn `--timeout` 120 → 300 (`config.py:20`), enforced by `tests/test_worker_timeout_invariant.py`. |
| Process-local jobs | **OPEN — this is V3.0 Slice 1-2.** Still `BackgroundTasks` at six call sites. |
| Frontend-only numeric guard | **OPEN — V3.0 Slice 3.** The guard is real and works, but canonical reconciliation belongs server-side; the frontend copy should be defence in depth. |
| Inaccurate research-stage mapping | **PARTIALLY RESOLVED.** `research_job.stage_for_node:158` and `stage_label:163` give a real node→stage map; V3 extends it to worker-reported stages. |
| OAuth stale-callback / `code_already_used` | **RESOLVED.** PR #187 shipped a bounded replay registry and 307→303. |
| Orphaned-job detection latency | **RESOLVED.** `is_orphaned:227` reads a job whose process is gone as dead immediately rather than after 45 minutes. |

---

## 3. Target architecture

```
                        InvestingBuddy Web (Next.js — largely unchanged)
                                        │
                                Research API (FastAPI)
                                        │  commit job, return 202
                                        ▼
                        ┌───────────────────────────────┐
                        │  Durable queue (Service Bus)  │  ← V3.0
                        │  + PostgreSQL job state       │
                        └───────────────────────────────┘
                                        │  lease
                                        ▼
                              Research Worker process
                                        │
                                Research Director            ← V3.5
                       (entity → playbook → prior research
                        → questions → workstreams → budget)
                                        │
                          Research Plan / Research Tasks
                                        │
        ┌───────────────────────────────┼───────────────────────────────┐
        ▼                               ▼                               ▼
  Native structured             Research Corpus search           External research
  data (SEC/XBRL,               (lexical + semantic,             (SearchProvider,
  Stooq, GLEIF)                 entity/period/scope              ResearchProvider)
        │                        filtered)  ← V3.1                      │  ← V3.4
        │                               │                               │
        │                               │                    ResearchLead (unverified)
        │                               │                               │
        └───────────────────────────────┴───────────────────────────────┘
                                        │
                          Independent retrieval + verification
                                        │
                     Canonical Corpus + Canonical Facts  ← V3.1/V3.2
                                        │
                        Deterministic Calculation Engine  ← V3.3
                                        │
                              Research Ledger  ← V3.5
                      (questions, findings, gaps, disagreements)
                                        │
                              Evidence Gap Review
                                        │
                        ┌───────────────┴───────────────┐
                        │  enough evidence?             │
                        │   NO → bounded follow-up ─────┘ (max N rounds)
                        │   YES ↓
                        └───────────────────────────────┐
                                        │
                              Evidence Verification  ← V3.0/V3.3
                                        │
                              Investment Council V2  ← V3.7
                                        │
                          Red Team challenge → analyst response
                                        │
                                      Chair
                                        │
                       Existing investor-facing report UX (V2)
                                        │
                          Research Memory / ResearchDelta  ← V3.8
```

### 3.1 Component boundaries

Six layers, and the boundaries between them are the architecture:

| Layer | Owns | Must not |
|---|---|---|
| **API** | HTTP contracts, auth, job submission, polling. | Run multi-minute work. Perform verification. |
| **Orchestration** | Job durability, leasing, retries, cancellation, budgets, stage reporting. | Know anything about investing. |
| **Research domain** | Entity identity, corpus, facts, calculations, ledger, memory, playbooks, verification, Council methodology. | Depend on a vendor SDK or a model name. |
| **Provider adapters** | Talking to OpenAI / DeepSeek / Gemini / Claude / Exa / Perplexity / Azure AI Search / pgvector. | Decide what is true, what a source tier is, or what enters the record. |
| **Retrieval** | Fetching bytes safely (SSRF, redirects, byte caps, DNS pinning) and turning them into documents. | Trust fetched content as instructions. |
| **Presentation** | Rendering the research state for a human. | Reconcile numbers (ADR-037 — the product layer *presents* the research state, it never reconciles it). |

The load-bearing rule: **the research domain imports provider *interfaces*, never
provider *SDKs*.** If swapping Exa for Perplexity requires editing anything under
`app/services/research/`, the abstraction has failed.

### 3.2 Directory shape (`V3 TARGET`)

Additive. Nothing under the existing tree is moved in V3.0-V3.2.

```
apps/api/app/
├── services/
│   ├── jobs/                 # V3.0 durable job contract + worker executor
│   │   ├── job_contract.py   # pure state machine, no I/O
│   │   ├── job_store.py      # PostgreSQL persistence + leasing
│   │   └── worker.py         # broker-agnostic executor loop
│   ├── corpus/               # V3.1 documents, versions, pages, chunks, tables
│   │   ├── models/           # domain types (not ORM)
│   │   ├── ingest.py
│   │   └── search/           # SearchBackend interface + adapters
│   ├── entities/             # V3.2 legal entity, security, listing, identifier
│   ├── tools/                # V3.3 typed read-only agent tools
│   ├── calc/                 # V3.3 deterministic calculation engine
│   ├── providers/            # V3.4 Model/Research/Search/Browser providers
│   ├── ledger/               # V3.5 run, task, question, finding, gap, disagreement
│   ├── playbooks/            # V3.6 versioned industry configuration
│   └── memory/               # V3.8 prior research + ResearchDelta
```

---

## 4. Durable execution (V3.0 — the priority)

### 4.1 Why this is first

Every later capability makes runs longer. Autonomous investigation with
follow-up rounds is minutes-to-tens-of-minutes of work. Building that on a
`BackgroundTasks` call inside a web worker means building it on something that a
routine App Service recycle deletes. Research methodology must not change until
the thing that runs it can survive a restart.

### 4.2 Target

```
FastAPI  ──commit job──▶  PostgreSQL (job record)
   │                              │
   └──enqueue──▶ Azure Service Bus ──▶ Research Worker ──lease/heartbeat──▶ PostgreSQL
```

Requirements, each of which is a testable property:

| Property | Meaning |
|---|---|
| Committed before response | The job row is committed **before** the API returns 202. A client that got a job id always has a durable job. |
| Idempotent submission | An `idempotency_key` makes a duplicate submit *join* the existing job rather than start a second expensive run. Preserves the V2 `in_flight_job_for_company` behaviour (`company_research_service.py:385`) but generalises it. |
| Leased execution | A worker *claims* a job for a bounded lease and heartbeats. An expired lease is reclaimable — that is what makes recovery automatic instead of manual. |
| Bounded retries | `attempt` / `max_attempts` with backoff via `available_at`. Transient vs permanent uses the taxonomy already in `llm/client.py:112-121`. |
| Dead-letter | A job that exhausts attempts lands in `dead_letter` with a reason — visible, not silently lost. |
| Cancellation | `cancel_requested` is checked at task boundaries. Cooperative, not `kill -9`. |
| Stage observability | The worker reports stage transitions using the existing `stage_for_node` / `stage_label` vocabulary so the UI contract does not fork. |
| CPU isolation | Extraction runs off the event loop (already true via `to_thread`) and, in the worker topology, off the API process entirely. |

### 4.3 What is reused, not rebuilt

`research_job.py` already owns the status vocabulary, the staleness derivation and
`interrupted`. V3 **keeps that vocabulary verbatim** — `pending`, `running`,
`completed`, `completed_with_warnings`, `failed`, derived `interrupted` — and adds
only the columns durability actually requires. Inventing a parallel status
vocabulary would fork the polling contract the web app already speaks.

### 4.4 Broker choice

Azure Service Bus is the Azure-native default and is already named in the V2
architecture as a later phase. It is an `OPEN DECISION` only in topology (queue
vs topic, one worker app vs per-job-type), not in vendor — see
[OPEN_DECISIONS.md](OPEN_DECISIONS.md#2-service-bus-worker-topology). The job
contract is broker-agnostic by construction: `job_store` exposes claim/heartbeat/
complete, and a broker is a *delivery hint*, not the source of truth. A
PostgreSQL-only polling worker is therefore a valid degraded mode and is what the
first slices use, which keeps V3.0 testable with zero cloud dependencies.

---

## 5. Research Corpus (V3.1)

See [DATA_AND_EVIDENCE_ARCHITECTURE.md](DATA_AND_EVIDENCE_ARCHITECTURE.md#2-research-corpus)
for the full model. Architecturally:

- Raw artifacts go to Blob Storage (already provisioned; `ExtractedDocument.blob_path`
  exists but is unused as a retrieval path).
- Parsed text is persisted **in full**, page- and section-addressed, not just the
  20 bounded excerpts V2 keeps.
- Retrieval is **hybrid** (lexical + semantic) and always filterable by entity,
  period, scope, source tier, document type and date. Vector-only retrieval is
  forbidden: a semantic match that cannot be filtered to "Group, FY2025" is not
  usable evidence in this domain.
- Every retrieved chunk carries enough lineage to render a citation without a
  second lookup.

---

## 6. Multi-provider research runtime (V3.4)

Full treatment in [PROVIDER_AND_MODEL_STRATEGY.md](PROVIDER_AND_MODEL_STRATEGY.md).
Architecturally the key points are:

1. **Four independent abstractions**, because they change independently:
   `ModelProvider` (completion), `SearchProvider` (find URLs), `ResearchProvider`
   (managed multi-step investigation), `BrowserProvider` (render JS pages).
   Coupling a research model to a search vendor is the mistake this prevents.
2. **One canonical result contract** — `ResearchProviderResult` — so a DeepSeek
   investigation and a Gemini Deep Research run reduce to the same shape.
3. **Provider output is a lead, never evidence.** The promotion path is
   enforced in code, not in a prompt.
4. **Transport ≠ content.** If Exa returns a Reuters article, the source tier is
   Reuters'. If SEC EDGAR returns a filing, the tier is the filing's. The
   fetching mechanism must never determine the trust level of what it fetched.
   This is why `Source` needs both a transport and a content-origin field
   (see [DATA_AND_EVIDENCE_ARCHITECTURE.md](DATA_AND_EVIDENCE_ARCHITECTURE.md#5-source--evidence-model-v3)).

---

## 7. Research modes and budgets

| Mode | Stack | Bounded by |
|---|---|---|
| `QUICK` | Official structured data + corpus retrieval + limited web enrichment + cheap model. | Latency first. |
| `STANDARD` | Native sources + corpus + SearchProvider + cheap specialist investigators + deterministic calculations + stronger Chair. | The default professional budget. |
| `DEEP` | Adds a managed Deep Research provider, broader retrieval, deeper verification, strong Council. | User-triggered. |
| `MAX` / due diligence | Multiple independent providers, more evidence rounds, strongest route, deeper human review. | `DEFERRED` past initial V3. |

Every mode carries a `ResearchBudget`. Consumption is tracked in **vendor-neutral
units** (tokens, calls, searches, fetches, documents, pages, OCR pages, index
queries, browser minutes, elapsed seconds) with cost as a *derived* figure, so a
price change is a config change and never a code change. No agent researches
indefinitely: the loop terminates on the first exhausted limit and says which one.

---

## 8. Non-negotiable invariants carried forward

These are V2 behaviours that V3 must not weaken. They are tested today and the
tests stay.

**Evidence.** Evidence before prose. Missing means missing. No fabricated data.
Model output is not canonical evidence. Factual claims require provenance.
Conflicts fail closed.

**Period.** Annual ≠ interim ≠ quarter; half ≠ quarter. Split-year semantics
preserved. Unknown remains unknown. No silent annualization. No guessed period
compatibility.

**Scope.** Group ≠ segment. Unknown scope ≠ Group. Scope flows through
calculations, search filters, evidence selection and verification — it is not a
display label.

**Source quality.** Transport quality is separate from content quality.

**Safety.** Human review required. No automatic BUY/SELL/HOLD. No autonomous
trading. No unsupported price target. No fabricated fair value. No
issuer-specific hacks.

---

## 9. Architecture acceptance check (§59)

| # | Question | Answer |
|---|---|---|
| 1 | Company outside today's registry discoverable? | YES — V3.2 entity master + universe generation. |
| 2 | Multiple listings → one legal issuer? | YES — `LegalEntity` / `Security` / `SecurityListing` (V3.2). |
| 3 | Full annual report searchable months later? | YES — V3.1 corpus with full parsed text + hybrid search. |
| 4 | Agents retrieve new evidence during research? | YES — V3.3 read-only tools inside the V3.5 bounded loop. |
| 5 | Agents query official government/industry APIs? | YES — V3.4 `DatasetDefinition`/`SeriesDefinition`/`Observation`, incrementally sourced. |
| 6 | External Deep Research without becoming canonical truth? | YES — `ResearchLead` promotion path. |
| 7 | Providers swappable without rewriting the domain? | YES — four provider interfaces; domain imports interfaces only. |
| 8 | Biotech methodology ≠ luxury methodology? | YES — V3.6 versioned playbooks. |
| 9 | Deterministic calculations replace LLM arithmetic? | YES — V3.3 calculation engine with typed inputs and recorded provenance. |
| 10 | Actual peers compared? | YES — V3.x peer engine with typed, sourced relationships. |
| 11 | Management language compared over time? | YES — V3.4 transcripts in the corpus, addressable by period. |
| 12 | Private research indexed safely? | YES — V3.4 groundwork; access policy is a first-class field, and `sent_to_provider_X` is an explicit permission. |
| 13 | New run knows what changed? | YES — V3.8 `ResearchDelta`. |
| 14 | Red Team challenges specific findings? | YES — V3.7 challenges reference `ResearchFinding` ids, not paragraphs. |
| 15 | Unresolved disagreements reach the Chair? | YES — `ResearchDisagreement` is persisted and is a required Chair input. |
| 16 | Research survives an API process recycle? | YES — V3.0 durable queue + leased worker. |
| 17 | Provider cost measurable? | YES — vendor-neutral consumption units per run and per provider call. |
| 18 | Provider output independently verifiable? | YES — verification is the promotion gate, and `verification_survival_rate` is a benchmark metric. |
| 19 | V2 remains fully usable? | YES — `release/v2-current` + additive-only schema + feature flags defaulting off. |
| 20 | V3 isolated from `main` until approval? | YES — `develop/v3`, no promotion without explicit user acceptance. |

Nothing in the acceptance list is `NO`. `MAX` mode, portfolio-level features and
a valuation engine are `DEFERRED` and are not in the list.

---

## 10. Related documents

- Data, corpus, entities, facts, ledger, memory → [DATA_AND_EVIDENCE_ARCHITECTURE.md](DATA_AND_EVIDENCE_ARCHITECTURE.md)
- Director, investigators, tools, loop, Council, Red Team → [AGENTIC_RESEARCH_ARCHITECTURE.md](AGENTIC_RESEARCH_ARCHITECTURE.md)
- Providers, models, pricing, routing, benchmark → [PROVIDER_AND_MODEL_STRATEGY.md](PROVIDER_AND_MODEL_STRATEGY.md)
- Phases and slices → [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
- V2 safety → [MIGRATION_AND_COMPATIBILITY_PLAN.md](MIGRATION_AND_COMPATIBILITY_PLAN.md)
