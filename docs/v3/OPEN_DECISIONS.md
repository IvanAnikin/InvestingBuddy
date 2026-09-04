# InvestingBuddy V3 — Open Decisions Register

**Last reviewed:** 2026-09-04. Baseline `4b60e07`.

Every unresolved V3 decision. A decision leaves this register by becoming an ADR
in `docs/DECISIONS.md`.

Format per entry: **status** · **options** · **recommendation** · **blocking?** ·
**evidence needed** · **owner**.

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
- **Blocking?** Blocks V3.1.4.
- **Evidence needed:** retrieval quality on real PNDORA/CFR/ASML documents;
  operational cost of an additional Azure service on the current plan.
- **Owner:** user (cost) + agent (implementation).

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

## 9. Quartr vs Fiscal.ai

- **Status:** OPEN — not evaluated
- **Options:** Quartr; Fiscal.ai; neither.
- **Recommendation:** defer until §8 establishes that direct retrieval is
  insufficient. Do not buy coverage before measuring the gap.
- **Blocking?** No.
- **Evidence needed:** pricing, API terms, European coverage, redistribution rights.
- **Owner:** user.

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

## 12. Raw page and document retention

- **Status:** OPEN
- **Options:** (a) full raw bytes indefinitely; (b) bytes with TTL, text
  indefinitely; (c) text only, no raw bytes.
- **Recommendation:** **(b)**. Raw bytes make re-extraction possible when the
  parser improves — which has already happened repeatedly (pipeline version is at
  15). Text-only would have made several past correctives impossible without
  re-fetching documents that may no longer be online. TTL bounds the storage bill.
- **Blocking?** Blocks V3.1.1.
- **Evidence needed:** Blob storage cost at expected corpus size; licence
  constraints per source class.
- **Owner:** user (cost) + agent.

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
