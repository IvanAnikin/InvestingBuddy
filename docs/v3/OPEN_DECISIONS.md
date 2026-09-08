# InvestingBuddy V3 — Open Decisions Register

**Last reviewed:** 2026-09-05. Baseline `4b60e07`.

Every unresolved V3 decision. A decision leaves this register by becoming an ADR
in `docs/DECISIONS.md`.

Format per entry: **status** · **options** · **recommendation** · **blocking?** ·
**evidence needed** · **owner**.

---

## Resolution round, 2026-09-05 — the user resolved eleven decisions at once

The governing constraint, stated by the user and now the frame for every provider
question in V3:

> **V3 must require no new paid SaaS subscriptions or commercial data
> subscriptions.**

Three paid capabilities are approved and already available: **Claude Code CLI**
(development and orchestration only), the **existing Azure OpenAI infrastructure**,
and **DeepSeek API usage** on pay-as-you-go. Everything else on the candidate list —
Exa, Perplexity, Gemini API, Anthropic API for production, Quartr, Fiscal.ai,
Browserbase, Apify, Parallel, AlphaSense — is **not purchased and not required**, and
the V3 Release Candidate must not depend on any of them.

Their abstractions stay. That is the point of having had them: each becomes a small
adapter behind a finished interface if it is ever approved, and none of them blocks
anything now.

| # | Was | Now |
|---|---|---|
| [1](#1-azure-ai-search-vs-postgresql--pgvector) | open (cost) | **RESOLVED → PostgreSQL + pgvector** ([ADR-047](../DECISIONS.md)) |
| [2](#2-service-bus-worker-topology) | open (cost) | **RESOLVED → PostgreSQL polling; no broker required** |
| [3](#3-exa-vs-perplexity-search) | open (spend) | **RESOLVED → both DEFERRED; DeepSeek `web_search` is the primary external search path** ([ADR-048](../DECISIONS.md)) |
| [4](#4-deepseek-data-governance-policy) | open (legal/comfort) | **RESOLVED → approved; rights metadata governs, not geography** ([ADR-049](../DECISIONS.md)) |
| [5](#5-openai-model-routing) | open | **RESOLVED → existing Azure OpenAI only, as the strong-model fallback** ([ADR-050](../DECISIONS.md)) |
| [6](#6-gemini-deep-research-role) | open | **RESOLVED → DEFERRED / NOT ACTIVATED** |
| [7](#7-claude-red-team-role) | open | **RESOLVED → Claude is not a production provider** |
| [8](#8-transcript-provider) | open (spend) | **RESOLVED → free/public issuer sources only; missing means missing** ([ADR-051](../DECISIONS.md)) |
| [9](#9-quartr-vs-fiscalai) | open | **RESOLVED → neither** |
| [11](#11-private-data-external-model-policy) | open (judgement) | **RESOLVED → per-document rights metadata decides; fail closed when unknown** ([ADR-049](../DECISIONS.md)) |
| [13](#13-model-cost-thresholds) / [14](#14-research-mode-budgets) | open (budget) | **RESOLVED → bounded technical defaults per research mode; no business budget now** ([ADR-052](../DECISIONS.md)) |
| [16](#16-future-valuation-scope) | open | **RESOLVED → factual context and deterministic multiples only** |

Four remain open, and **none blocks any remaining work**:
[10](#10-openfigi-usage-and-licensing) (OpenFIGI — FIGI stays representable, not
obtainable), [12](#12-raw-page-and-document-retention) (retention TTL — the primitive
ships unconfigured), [15](#15-monitoring-cadence) (V3.9 scheduling),
[17](#17-ci-coverage-for-the-v3-branch) and [18](#18-fate-of-docsdata_source_inventorymd--xlsx).

---

## 1. Azure AI Search vs PostgreSQL + pgvector

- **Status:** OPEN
- **Options:** (a) Azure AI Search — managed hybrid search, native semantic
  ranking, another service and bill; (b) PostgreSQL + `pgvector` — one datastore,
  `tsvector` lexical + vector similarity, self-managed ranking quality.
- **Recommendation:** **start with (b)**. The corpus at private-use scale is
  thousands of documents, not millions; PostgreSQL is already provisioned,
  already backed up, and keeps filters (entity, period, scope) in the same
  transaction as the data they filter. Revisit if hybrid ranking quality proves
  inadequate on the real-issuer set. Either way the `SearchBackend` interface
  means the choice costs one adapter, not a rewrite.
- **Blocking?** Blocks the production backend only. **V3.1.4 shipped without
  taking it:** the interface, the fusion, the query rules and a complete in-memory
  reference backend are done, and slices 1.5-1.6 are written against them.
- **Evidence needed:** retrieval quality on real PNDORA/CFR/ASML documents;
  operational cost of an additional Azure service on the current plan.
- **Owner:** user (cost) + agent (implementation).
- **Status 2026-09-04:** still OPEN and **deliberately not resolved by the agent.**
  The slice register originally paired 1.4 with "a PostgreSQL lexical
  implementation"; that was **not** built, because both options in this decision
  cover the lexical layer and a `tsvector` backend would be choosing (b) in
  everything but name — same datastore, same operational model, same "one less
  service" argument the decision turns on.
  [Slice 1.4](slices/V3.1-4-search-interface.md) ships the abstraction instead,
  and `test_the_production_backend_decision_is_not_taken_here` asserts the
  backends directory still contains only the in-memory reference — so adding an
  adapter fails a test and forces the decision to be taken deliberately.
  What the in-memory backend proves is that the contract is *sufficient*; it
  proves nothing about quality or scale, and a corpus of thousands of annual
  reports is not served from a Python dict.
- **RESOLVED 2026-09-05 → (b) PostgreSQL + `pgvector`.** [ADR-047](../DECISIONS.md).
  The user's reasoning, recorded verbatim in substance: PostgreSQL already exists in
  the architecture, Azure PostgreSQL supports `pgvector`, it avoids another paid
  managed service, it keeps canonical corpus metadata and retrieval close together,
  and it is lower operational complexity at the current scale. The `SearchBackend`
  abstraction preserves a later move to Azure AI Search if scale or quality demands
  it, so this is reversible for the price of one adapter.
  **Azure AI Search is not provisioned.** Hybrid remains lexical + vector + the
  platform's own rank fusion + metadata filters, exactly as the interface already
  expresses it. If enabling the `pgvector` extension would modify the deployed
  database, the migration is written and scratch-validated and **not applied** —
  the campaign's standing restriction is unchanged.

## 2. Service Bus worker topology

- **Status:** OPEN
- **Options:** (a) one queue, one worker app, job type in the message; (b) queue
  per job type with independent scaling; (c) PostgreSQL polling only, no broker.
- **Recommendation:** **(c) first, then (a)**. The job store is the source of
  truth and the broker is only a delivery hint, so (c) is a genuinely valid
  production mode at this volume and it makes V3.0 testable with zero cloud
  dependencies. (b) is premature at one user.
- **Blocking?** Blocks V3.0.6 only. V3.0.1-0.5 proceed regardless.
- **Evidence needed:** B1 plan headroom (~1.75 GB, already 93-95% with one
  worker); whether a separate worker App Service is affordable.
- **Owner:** user (cost) + agent.
- **RESOLVED 2026-09-05 → (c), and no broker is required for the Release
  Candidate.** The PostgreSQL-backed durable worker from V3.0 continues unless
  testing proves it fundamentally insufficient, which it has not. The broker
  abstraction stays as a later scale-out option. **Azure Service Bus is not
  provisioned**, and slice 0.6 is `DEFERRED` rather than `BLOCKED` — there is nothing
  left to decide. The Release Candidate must be capable of running without a new
  broker service, and it is.

## 3. Exa vs Perplexity Search

- **Status:** OPEN
- **Options:** Exa `/search` $7/1k + `/contents` $1/1k pages; Perplexity Search
  API $5/1k, `web_search` tool $2.50/1k, `fetch_url` $0.50/1k. *(verified 2026-09-04)*
- **Recommendation:** **benchmark both; start with Exa.** Perplexity is 30-50%
  cheaper on both axes, so Exa must justify itself on retrieval quality —
  specifically neural relevance for "find evidence that X" and date/domain
  filtering that maps onto corpus filters. If Exa does not win on
  `cost_per_verified_finding`, switch.
- **Blocking?** Blocks V3.4.2 defaults, not the interface.
- **Evidence needed:** benchmark §7 of the provider strategy on MRNA/CFR/ASML.
- **Owner:** agent (benchmark) → user (spend approval).
- **RESOLVED 2026-09-05 → neither. Both DEFERRED.** [ADR-048](../DECISIONS.md).
  **DeepSeek's server-side `web_search` is the primary external general-web research
  and search path** for initial V3, so neither subscription is purchased and neither
  is required. The `SearchProvider` interface and its fake (slice 4.1) stay, and an
  Exa or Perplexity adapter remains a small future slice if either is ever approved.
  **No credentials are required and V3.4 is not blocked.**
  The acquisition hierarchy is now explicit and ordered:
  existing corpus → official structured API → official regulator/issuer source →
  the current safe direct fetcher → **DeepSeek web search** → source URL retrieval
  *through InvestingBuddy* → verification.
  A DeepSeek search result or model claim is a `ResearchLead` until the platform has
  retrieved and verified the underlying source. **A search snippet is never canonical
  evidence.**

## 4. DeepSeek data-governance policy

- **Status:** OPEN — **provisionally restricted**
- **Options:** (a) public content only; (b) public + issuer documents; (c) any
  content after terms review; (d) not used.
- **Recommendation:** **(a) until the terms are read and recorded.** Note that the
  price case for DeepSeek is weaker than assumed: at InvestingBuddy's ~6k-in/2.2k-out
  call shape it is 1.4× cheaper than `gpt-5.6-luna` off-peak and **30% more
  expensive at peak**, and runs are user-triggered so off-peak cannot be chosen.
  It must therefore win on research quality, not on price.
- **Blocking?** Blocks V3.4.3 enablement, not the adapter.
- **Evidence needed:** official data-handling, jurisdiction and retention terms;
  benchmark quality results.
- **Owner:** user (legal/comfort) + agent (benchmark).
- **RESOLVED 2026-09-05 → approved, and the rule is about rights rather than
  geography.** [ADR-049](../DECISIONS.md). DeepSeek is the **primary external
  research/model provider** on pay-as-you-go usage, and China location/storage is
  explicitly **not** a blocker for this project.
  DeepSeek may process `public_official`, `public_issuer`, `public_web`,
  InvestingBuddy-derived research context, **and user-private content when that
  document's own policy metadata allows third-party model processing.**
  It must never receive API keys, passwords, credentials, secrets, authentication
  tokens, private system configuration, a document whose licence or rights metadata
  forbids external-model processing, or anything marked
  `external_model_allowed=false`.
  So: **data rights and explicit source policy govern DeepSeek use, not provider
  geography** — and the previous blanket `user_private → DENY` rule is retired in
  favour of the per-document one. **Fail closed when the policy is unknown.**

## 5. OpenAI model routing

- **Status:** OPEN
- **Options:** which of `gpt-6-astra` / `gpt-5.6-sol` / `gpt-5.6-terra` /
  `gpt-5.6-luna` fills each slot; Azure OpenAI deployment vs direct API; whether
  Batch (50%) is used for non-urgent work.
- **Recommendation:** Chair `gpt-5.6-sol` escalating to `gpt-6-astra` for
  contradictions; classification `gpt-5.6-luna`; document reasoning
  `gpt-5.6-terra`. Stay on Azure OpenAI where the credential path and TPM quota
  already exist. Absolute cost is small (~$0.19/report for a cheap-analyst +
  strong-Chair split), so route for **rate-limit headroom**, not for the bill.
- **Blocking?** No — slots are configuration.
- **Evidence needed:** Azure TPM quota per deployment; benchmark quality.
- **Owner:** agent, with user cost sign-off.
- **RESOLVED 2026-09-05 → the existing Azure OpenAI infrastructure only, as the
  strong-model fallback.** [ADR-050](../DECISIONS.md). **No new OpenAI commercial
  account is created.** The routing policy is explicit:
  `cheap/bulk research → DeepSeek`, `difficult final reasoning → Azure OpenAI`.
  Azure OpenAI's intended V3 role is stronger synthesis when needed, difficult
  Research Director cases, complex evidence contradictions, Red Team where
  appropriate, the Chair and final Council synthesis, and any task where a benchmark
  shows it materially outperforms the cheaper route. **Not every research subtask
  goes to the expensive model.** Routing stays configurable, and the slots remain
  the only thing domain logic names.

## 6. Gemini Deep Research role

- **Status:** OPEN
- **Options:** (a) `DEEP` mode only; (b) `DEEP` + `MAX`; (c) not used.
- **Recommendation:** (a), as a contractor producing `ResearchLead`s only. The
  **5,000 free grounded search requests/month shared across Gemini 3.x** is
  materially valuable at this platform's volume and is the strongest reason to
  wire it.
- **Blocking?** No.
- **Evidence needed:** Deep Research API availability and terms; whether cited
  sources are traceable enough to verify.
- **Owner:** agent → user.
- **RESOLVED 2026-09-05 → `OPTIONAL / DEFERRED / NOT ACTIVATED`.** Not required for
  the V3 Release Candidate. The `ResearchProvider` abstraction stays; no credentials
  are required and nothing is purchased. **DeepSeek provides the initial autonomous
  web-research capability**, and V3.5-V3.9 are not blocked on Gemini.

## 7. Claude Red Team role

- **Status:** OPEN
- **Options:** (a) Red Team only; (b) Red Team + independent analyst; (c) not used.
- **Recommendation:** (a). Vendor diversity is most of the value of a Red Team —
  a model challenging its own family's output shares its blind spots. Sonnet 5
  ($2/$10 per MTok) is the sensible entry point. Claude Code remains a
  development tool and does not become the production backend.
- **Blocking?** No.
- **Evidence needed:** benchmark on challenge quality.
- **Owner:** agent → user.
- **RESOLVED 2026-09-05 → Claude is not a production provider.** Claude Code CLI is
  authorised for campaign orchestration, coding, review, development-time research,
  testing and implementation agents — and **must not become an InvestingBuddy runtime
  dependency**. No Anthropic API production integration is required for V3, and the
  consumer product must not be automated from the application.
  `ClaudeResearchProvider` may remain a future adapter and a fake.
  A consequence worth stating: the Red Team's vendor-diversity argument is **not
  currently being had**. With DeepSeek and Azure OpenAI as the two available vendors,
  the Red Team can at least be a *different vendor from the Chair*, and
  `ModelRouter.shares_vendor_with` is what reports whether it is.

## 8. Transcript provider

- **Status:** OPEN
- **Options:** (a) direct issuer retrieval; (b) a vendor; (c) hybrid.
- **Recommendation:** (c) — direct issuer first (already the trusted path,
  already free), vendor as fallback for coverage.
- **Blocking?** Blocks V3.4.8.
- **Evidence needed:** issuer transcript availability across the regression set;
  vendor coverage for European issuers specifically, which is where the current
  pipeline is thinnest.
- **Owner:** user (spend) + agent.
- **RESOLVED 2026-09-05 → (a) direct issuer and public sources only.**
  [ADR-051](../DECISIONS.md). **No paid Quartr or Fiscal.ai subscription.** The
  canonical transcript/IR-event architecture is implemented anyway, and acquisition
  uses free, legally accessible public sources: issuer IR sites, issuer-published
  transcripts, earnings releases, presentations, capital-markets-day materials,
  regulatory filings and public event documents.
  When a transcript is not publicly available: **missing means missing.** It is not
  fabricated, and a paywalled source is not scraped. Vendor adapters remain future
  optional integrations.

## 9. Quartr vs Fiscal.ai

- **Status:** OPEN — not evaluated
- **Options:** Quartr; Fiscal.ai; neither.
- **Recommendation:** defer until §8 establishes that direct retrieval is
  insufficient. Do not buy coverage before measuring the gap.
- **Blocking?** No.
- **Evidence needed:** pricing, API terms, European coverage, redistribution rights.
- **Owner:** user.
- **RESOLVED 2026-09-05 → neither.** No commercial transcript subscription is part of
  V3. See [#8](#8-transcript-provider).

## 10. OpenFIGI usage and licensing

- **Status:** OPEN
- **Options:** OpenFIGI for FIGI mapping; GLEIF only (already live) for LEI;
  both.
- **Recommendation:** GLEIF is already a live source and covers legal-entity
  identity. Add OpenFIGI only if instrument-level mapping proves necessary in
  V3.2.
- **Blocking?** Blocks part of V3.2.1 only if instrument identifiers are required
  at that point.
- **Evidence needed:** OpenFIGI terms; whether ISIN alone suffices.
- **Owner:** agent → user.

## 11. Private-data external-model policy

- **Status:** OPEN — **default deny**
- **Options:** (a) never; (b) per-document opt-in; (c) per-provider allowlist;
  (d) (b)+(c).
- **Recommendation:** **(d)**. Both dimensions are required: "external models are
  allowed" and "this provider, in this jurisdiction, under these retention terms,
  is allowed" are different questions. Enforced at the tool boundary, per
  document — a per-run flag is exactly the coarse control that leaks one document.
- **Blocking?** Blocks private-research ingestion enablement.
- **Evidence needed:** user's comfort threshold; per-provider terms.
- **Owner:** **user** — this is a judgement call, not a technical one.
- **RESOLVED 2026-09-05 → (d), per document *and* per provider, decided by the
  document's own rights metadata.** [ADR-049](../DECISIONS.md). Private research
  ingestion is now first-class rather than gated.
  Every document's default policy must explicitly represent: **external model
  permitted (yes/no)**, **permitted providers when constrained**, **retention**,
  **indexing**, **quoting** and **deletion**.
  A user-owned document explicitly allowed for external models **may** be used by
  DeepSeek. A licensed third-party report follows its own rights metadata.
  **Rights are never inferred from the fact that a file was uploaded**, and an
  unknown policy fails closed.

## 12. Raw page and document retention

- **Status:** OPEN
- **Options:** (a) full raw bytes indefinitely; (b) bytes with TTL, text
  indefinitely; (c) text only, no raw bytes.
- **Recommendation:** **(b)**. Raw bytes make re-extraction possible when the
  parser improves — which has already happened repeatedly (pipeline version is at
  15). Text-only would have made several past correctives impossible without
  re-fetching documents that may no longer be online. TTL bounds the storage bill.
- **Blocking?** Shapes V3.1.1's *retention*, not its existence. **No longer
  blocking:** Slice 1.1 shipped the primitive rather than the answer.
- **Evidence needed:** Blob storage cost at expected corpus size; licence
  constraints per source class.
- **Owner:** user (cost) + agent.
- **Status 2026-09-04:** still OPEN and **deliberately not resolved by the agent.**
  [Slice 1.1](slices/V3.1-1-raw-artifact-store.md) implements the configurable
  primitive: `V3_ARTIFACT_RETENTION_DAYS` defaults to **0**, which stamps
  `retention_expires_at` as NULL and reads as "no TTL configured" — never as
  "keep forever as a policy". An explicit, callable `expire_artifacts(...)` sweep
  exists, defaults to `dry_run=True`, and **nothing schedules it**; a test asserts
  no other module in the codebase even calls it. Applying option (b) later is
  therefore a settings change, not a migration. Option (c) is also representable
  without code changes: `V3_ARTIFACT_STORE_BACKEND=none` records lineage and stores
  no bytes at all, which is the default.

## 13. Model cost thresholds

- **Status:** OPEN
- **Options:** per-run cap; per-day cap; per-month cap; combination.
- **Recommendation:** per-run cap by mode **plus** a monthly ceiling. The per-run
  cap bounds a runaway loop; the monthly ceiling bounds a runaway *user*. Note
  from §5 of the provider strategy that **retrieval, not tokens, is the dominant
  cost at research scale** — a 60-search DEEP run spends ~$0.45 on search against
  ~$0.10 on cheap-model reasoning, so the search cap is the one that matters.
- **Blocking?** Blocks V3.0.5 defaults.
- **Evidence needed:** the user's actual monthly budget.
- **Owner:** **user**.
- **RESOLVED 2026-09-05 → bounded technical defaults now; no business budget
  chosen.** [ADR-052](../DECISIONS.md). Sensible configurable maxima exist for
  research rounds, web searches, tool calls, documents, tokens, wall time and
  DeepSeek calls, per research mode.
  Two things are explicit in the user's instruction and both are recorded here
  because they pull in opposite directions: **the absence of a monthly business
  budget is not unlimited execution**, and **V3 is not blocked on pricing or
  business-plan decisions**. User-facing quotas and pricing stay deferred until
  after the Release Candidate.

## 14. Research-mode budgets

- **Status:** OPEN
- **Options:** concrete numbers for QUICK / STANDARD / DEEP.
- **Recommendation:** derive from measured live runs rather than guessing. Current
  measured anchors: a full company research run is 261-451s; ingestion alone ~154s;
  a council ~145-190s. Set initial budgets ~1.5× the measured envelope, then
  tighten.
- **Blocking?** Blocks V3.0.5 defaults.
- **Evidence needed:** measured consumption from the first instrumented runs.
- **Owner:** agent → user.
- **RESOLVED 2026-09-05 → technical presets, not price tiers.**
  [ADR-052](../DECISIONS.md). QUICK / STANDARD / DEEP / MAX are **research-depth
  presets** with no subscription price attached:
  QUICK is mostly native sources and the corpus with minimal DeepSeek research;
  STANDARD adds bounded DeepSeek investigation plus Azure OpenAI synthesis where
  justified; DEEP widens the DeepSeek rounds and source retrieval with stronger final
  synthesis; MAX takes the highest bounded limits and is **still finite**.
  No external managed Deep Research subscription is required for any of them.

## 15. Monitoring cadence

- **Status:** OPEN — V3.9, far out
- **Options:** daily; weekly; event-driven on new filings; user-configured.
- **Recommendation:** event-driven on new filings, plus a user-configurable
  scheduled refresh. Polling on a timer mostly re-reads unchanged documents.
- **Blocking?** No.
- **Owner:** user.

## 16. Future valuation scope

- **Status:** OPEN — **out of initial V3**
- **Options:** (a) none; (b) valuation *context* only (multiples with peers and
  history); (c) a DCF/fair-value engine.
- **Recommendation:** **(b)**. The existing valuation-guard role already provides
  context without a target. (c) requires separate approval and carries the
  regulated-advice risk the platform deliberately avoids.
- **Blocking?** No.
- **Owner:** **user**.

## 17. CI coverage for the V3 branch
- **RESOLVED 2026-09-05 → (b), factual valuation *context* only, and the existing
  safety boundary is preserved unchanged:** no automatic BUY/SELL/HOLD, no price
  targets, no projected returns, no unsupported fair value. Only factual valuation
  context and deterministic multiples where already valid and allowed. A dedicated
  valuation phase can be approved separately later. **Considered resolved for this
  V3 release, and V3 is not blocked on valuation methodology.**

## 17. CI coverage for the V3 branch

- **Status:** OPEN
- **Options:** (a) add `develop/v3` to the `branches` lists in `api-ci.yml` /
  `web-ci.yml`; (b) a separate V3 workflow; (c) local gates only.
- **Recommendation:** **(a)**. It is a two-line change and the alternative is
  relying on discipline for every slice. It does touch a file that is also on
  `main`, so it lands as its own small V3 slice and is documented as a V3-only
  change.
- **Blocking?** Not blocking, but every slice pays for it in manual work until
  resolved.
- **Evidence needed:** none — this is a preference about CI minutes.
- **Owner:** user.
- **Status 2026-09-04:** still OPEN and **deliberately not resolved by the agent**
  — the workflow files are unchanged, because they also live on `main` and the
  decision is the user's. What was done instead is
  [`scripts/v3-gates.sh`](../../scripts/v3-gates.sh): the exact commands both
  workflows run, in one command, so "it passed locally" means what CI would have
  meant. `mypy` is compared against a recorded baseline
  (`scripts/mypy-baseline.txt`, currently 71) rather than failed on a non-zero
  count — a gate that is red on every run trains a reader to ignore it. Every
  V3.0 slice ran through it and recorded the output in its slice document.

## 18. Fate of `docs/DATA_SOURCE_INVENTORY.md` / `.xlsx`

- **Status:** OPEN
- **Context:** both files were untracked in the working tree at V3 branch
  creation. They are the authoritative inventory of all 54 external sources and
  are referenced by the V3 architecture documents.
- **Options:** (a) commit to `main` (they describe V2's live state); (b) commit to
  `develop/v3`; (c) leave untracked.
- **Recommendation:** **(a)**. They document the *current deployed* system, so
  they belong on the branch that describes it.
- **Blocking?** No.
- **Owner:** **user**.

---

## V3.11 status of the provider decisions

Two decisions above are affected by what V3.11 measured, and neither is reopened here —
this records where they stand.

**#4 DeepSeek data-governance policy.** Unchanged as a policy. The private-document rule —
*a document may reach an external model only when its explicit rights/policy permits it,
and unknown policy fails closed* — remains in force and is **still untested against a live
call**, because no document has been sent to DeepSeek.

**#3 Exa vs Perplexity Search — RESOLVED, and V3.11.1.1's reversal is itself reversed.**

V3.11.1.1 reported that DeepSeek has no server-side web search and that the general-web
leg was therefore unstaffed. **That was wrong, and the error was one of scope**: the probe
only ever asked `POST /chat/completions`, which serves no builtin tools at all. DeepSeek's
builtin `web_search` lives on `POST /responses`, and V3.11.1.2 measured it live — it
issues queries, opens pages, reads them and reports the URLs it opened.

So ADR-048 stands as originally decided, now on measured evidence rather than on vendor
documentation: **DeepSeek's server-side `web_search` is the primary external general-web
search path.** Exa, Perplexity and Gemini remain deferred, unpurchased and not required.
**Nothing needs buying.** See [ADR-055](../DECISIONS.md#adr-055) and
[the slice](slices/V3.11-1-2-deepseek-responses-web-search.md).

Two properties of that contract are carried into the platform rather than assumed away:
it returns **no structured citations** (so a candidate is a page the provider actually
opened, and a URL cited only in prose is counted as a fabrication signal, never promoted),
and its **`max_tool_calls` and `filters.allowed_domains` are accepted and ignored** (so
spend and domain restrictions are enforced by InvestingBuddy and the telemetry says which
side enforced them).

The search leg stays behind `V3_DEEPSEEK_SEARCH_ENABLED=false`, which — after review
caught that the flag had no consumer in code — is now genuinely enforced in
`DeepSeekSearchProvider.search()`: with it off, the transport is never called and nothing
is spent. A verified capability is not a decision to spend on it; enabling it in an
acceptance run remains **your call**, and it is decision #20's sibling rather than a
defect.

**New, for the user.** Two items surfaced by V3.11 that are decisions rather than defects:

| # | Decision | Why it is yours |
|---|---|---|
| 19 | Should V2's HTML extraction get V3.11's container-block fix? | V2's excerpt path also gets **zero text** from modern Inline-XBRL SEC filings. Fixing it would change **deployed report content**, so V3.11 left V2 byte-identical. |
| 20 | Enabling DeepSeek's model leg | `v3_deepseek_model_enabled` (default off) routes Investigator and follow-up work to DeepSeek. The completion contract is **verified**, but no acceptance run has used it, so the cost figures and the three Councils in the report would no longer describe what runs. Needs its own acceptance slice. |
| 21 | **Rotate the validation key** — now for **two** reasons | (a) V3.11.1.1: the key reached terminal output through a dataclass `repr` before that was fixed. (b) V3.11.1.2: a live test failed on an assertion about an *innocent* field and pytest rendered the whole `Settings` object into the diff, key included. Both leak paths are now closed at the type — a named `CREDENTIAL_SETTING_FIELDS` list, every member `Field(repr=False)`, with tests that the list is complete and has not drifted. Review caught the first attempt covering only `*_api_key`, leaving `database_url` (the DB password) and `staging_basic_auth` printing. Nothing was ever committed — the real key appears in no tracked file. **It should still be rotated.** |
| 22 | ~~The `ResearchLead` path has no producer~~ **— CLOSED by V3.12, on real data**. It is wired and demonstrated: 5 of the last 6 live MRNA runs promoted external evidence into ledger findings — every non-promotion a correct refusal — and an 8-case negative acceptance minted exactly 1. What replaces it is narrower and recorded in the slice: **scope is unchecked on this path**, and promotion is provider-dependent. *(Historical note, kept because the campaign was wrong twice: V3.11.1.1 said the role could never be staffed; V3.11.1.2 said it was staffable and unstaffed.)* | — |
| 23 | ~~**V3 has no deterministic forbidden-language backstop**~~ **— DECIDED by the user 2026-09-08: accepted, not implemented.** | See the record below. |


---

## Decision #23 — resolved by the user, 2026-09-08

**The decision.** The user has decided that a deterministic forbidden-language /
recommendation-language backstop over V3 output is **not required** for V3 activation. It
is not to be implemented now, and it is not to be raised again as a release blocker.

**What that accepts, stated plainly.** The review that raised #23 said the backstop
"should be a required gate before any V3 output reaches a reader", and noted the risk was
theoretical only because V3 was undeployed. **V3.13 removes that qualifier**: the V3
Research panel puts V3 findings, the Chair's synthesis and external lead claims in front
of a reader on `/research/reports/[id]`.

The mechanism is worth stating exactly, because "V3 has no gate" is not quite right:

* V2's safety gate is `final_report_generator._validate_safety`, and it scans
  `report_content` — the sections of `content_markdown`.
* The V3 payload is attached to `source_summary_json["v3_research"]`, a different
  column. The gate never reads it.
* So V3 text reaches the reader without passing a deterministic scan, while every V2
  section on the same page has passed one.

**What still constrains V3 output**, so the accepted risk is described at its real size
rather than its worst-case one:

* The Chair's vocabulary is enforced after the fact — a label outside the closed set is
  rejected, and `deterministic_verdict` cannot emit one at all.
* A finding may only cite ids the tools returned, so open-web prose cannot enter the
  ledger as evidence; it enters as a `ResearchLead`, and the panel labels an unverified
  lead "Provider claim only".
* The V2 report above the panel is unchanged and still gated.

**What is genuinely unguarded**: free-text `statement`, `mechanism` and `synthesis`
fields, which are model prose, and lead `claim` text, which is vendor prose derived from
open-web sources. A recommendation phrased in one of those would reach the page.

**The cheapest future closure**, recorded so the decision can be revisited without
re-deriving it: `safety_terms.scan_value(outcome.to_dict())` in `attach_to_report`,
which is one call at a single choke point every V3 payload already passes through. It is
not implemented, by decision.

