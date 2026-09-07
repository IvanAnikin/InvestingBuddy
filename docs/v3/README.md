# InvestingBuddy V3 — Documentation Index

**Status:** V3 is IN DEVELOPMENT and UNAPPROVED. Nothing in this directory
describes deployed behaviour unless a section explicitly says `CURRENT`.

## Branch and release state

| Ref | Meaning |
|---|---|
| `main` | The current **approved** version. Deployed to `ib-stg-rg` (private-use production). V3 must never be merged here without explicit user approval. |
| `v2-final-pre-v3-2026-09-04` | Annotated tag pinning the V2 baseline commit `4b60e07`. Immutable. |
| `release/v2-current` | V2 preservation / maintenance branch, pinned to the same `4b60e07`. Emergency V2 fixes go here. |
| `develop/v3` | V3 integration branch. Unapproved, undeployed. |
| `feature/v3-*` | PR-sized V3 implementation slices. Merge target is `develop/v3` only. |

**Where the work stands (2026-09-07).** `develop/v3` carries **all eleven phases**,
V3.0 through V3.11, every one `IMPLEMENTED` — nothing is deployed and migrations 019-038
have reached no deployed environment.

V3.10 ran real issuers — **MRNA, CFR and ASML** — through the whole chain and ended
`NOT READY` against four blockers. **V3.11 closed all four.** All three issuers now
complete a **playbook-gated** Council on real public data; real-document scope coverage
went from 1.7% to 8.1% with a **0.0% false-positive Group rate**; a research run costs a
measured **$0.01453**; and DeepSeek is verified live across **both** its endpoints
(**16/16**) while remaining optional and flag-gated off.

The DeepSeek story is worth reading in order, because the campaign got it wrong once and
the error generalises: V3.11.1.1 probed `POST /chat/completions`, found no builtin tools,
and reported that DeepSeek **has no server-side web search**. It has one — on
`POST /responses` — and [V3.11.1.2](slices/V3.11-1-2-deepseek-responses-web-search.md)
measured it. **An absence measured on one endpoint is not an absence**
([ADR-055](../DECISIONS.md#adr-055)). Consequence for the plan: **no search provider needs
buying**, and Exa, Perplexity and Gemini stay deferred and unpurchased.

The recommendation is **`READY FOR USER ACCEPTANCE / MAIN PROMOTION REVIEW`**, in
[§12 of the release candidate report](V3_RELEASE_CANDIDATE_REPORT.md#12-recommendation).
Read **§11.6** first: it names the one thing still unproved. The decision is the user's.

**Start with [V3_RELEASE_CANDIDATE_REPORT.md](V3_RELEASE_CANDIDATE_REPORT.md).** It records
what was built, what was proved, and — in §7 and §10.5, the sections that matter — what was
**not**. §10 is the V3.10 product acceptance; §11 is the recommendation.

What was proved without a deployment: the whole 20-migration chain applies and **reverses**
against real PostgreSQL 16 with the downgraded schema byte-identical to V2's; **zero** V2
columns were removed or renamed and exactly **one** nullable column was added to an
existing V2 table; ORM/DDL drift across all 38 V3 tables is none; and the corpus still
takes the real 169-page Pandora Annual Report from 40 pages to 169 with the pre-reprocess
citation still resolving.

**Read [CAMPAIGN_STATE.md](CAMPAIGN_STATE.md) first.** It carries the verified Git and
migration state, the gate baseline, and the campaign's own account of itself.

```
                         main  ── deployed, approved
                          |
                          o  4b60e07  ← v2-final-pre-v3-2026-09-04
                         / \
        release/v2-current   develop/v3
                                 |
                           feature/v3-<phase>-<slice>-<name>
```

## Status labels used throughout these documents

| Label | Meaning |
|---|---|
| `CURRENT` | True of the code on `main` today. Verified against the repository, not inferred from older docs. |
| `V3 TARGET` | Designed but not yet built. |
| `IMPLEMENTED IN V3` | Built and merged into `develop/v3`. |
| `DEFERRED` | Deliberately out of the initial V3 scope. |
| `OPEN DECISION` | Tracked in [OPEN_DECISIONS.md](OPEN_DECISIONS.md). |
| `VENDOR-DEPENDENT` | Depends on a third-party capability or price that may change. |

Phase status uses: `NOT STARTED` → `IN PROGRESS` → `IMPLEMENTED` → `VALIDATED` → `APPROVED`.
Only explicit user acceptance can move the overall release to `APPROVED FOR MAIN`.

## Documents

| Document | What it answers |
|---|---|
| [ARCHITECTURE_SPEC.md](ARCHITECTURE_SPEC.md) | What V3 is, the target component topology, and how a research run flows end to end. |
| [DATA_AND_EVIDENCE_ARCHITECTURE.md](DATA_AND_EVIDENCE_ARCHITECTURE.md) | Research Corpus, entity master, source/evidence model, calculation engine, Research Ledger, Research Memory. |
| [AGENTIC_RESEARCH_ARCHITECTURE.md](AGENTIC_RESEARCH_ARCHITECTURE.md) | Research Director, specialist investigators, agent tool contracts, bounded investigation loop, Council V2, Red Team. |
| [INDUSTRY_PLAYBOOK_ARCHITECTURE.md](INDUSTRY_PLAYBOOK_ARCHITECTURE.md) | How industry methodology becomes versioned configuration rather than a prompt. |
| [PROVIDER_AND_MODEL_STRATEGY.md](PROVIDER_AND_MODEL_STRATEGY.md) | Provider abstraction, candidate vendors, verified pricing, model routing, the benchmark harness. |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | The phase order and the PR-sized slice register. |
| [MIGRATION_AND_COMPATIBILITY_PLAN.md](MIGRATION_AND_COMPATIBILITY_PLAN.md) | How V3 lands without breaking V2 reports, IDs, citations or the deployment. |
| [ACCEPTANCE_AND_TEST_STRATEGY.md](ACCEPTANCE_AND_TEST_STRATEGY.md) | Test gates, real-issuer regression set, external-API test safety. |
| [SECURITY_DATA_GOVERNANCE_AND_LICENSING.md](SECURITY_DATA_GOVERNANCE_AND_LICENSING.md) | Data classes, what may be sent to which provider, and the threat model additions. |
| [OPEN_DECISIONS.md](OPEN_DECISIONS.md) | Every unresolved decision, with owner and blocking status. |
| [V3_RELEASE_CANDIDATE_REPORT.md](V3_RELEASE_CANDIDATE_REPORT.md) | **The release candidate — read this first.** What was built, what was proved, and §7: what was *not*. |
| [CAMPAIGN_STATE.md](CAMPAIGN_STATE.md) | **Durable campaign memory.** Verified Git/migration/phase state, merged slices, correctives, open decisions by owner, and the next executable action. |

## The one-sentence definition

> InvestingBuddy V3 is an evidence-first autonomous investment-research operating
> system in which specialist research agents can plan investigations, use
> public/private/structured research tools, search and retrieve documents,
> perform deterministic financial analysis, identify evidence gaps, challenge
> conclusions, and build persistent institutional research memory before an
> Investment Council synthesizes the result for a human investor.

## The one rule that shapes everything else

> **External research agents discover and investigate. InvestingBuddy verifies,
> persists, calculates, reconciles, remembers and determines what may enter the
> canonical investment-research record.**

A model's output — from any vendor, including a managed Deep Research product —
is never automatically evidence. It enters as a `ResearchLead` and must survive
independent retrieval and verification before it can become a canonical fact.
See [DATA_AND_EVIDENCE_ARCHITECTURE.md](DATA_AND_EVIDENCE_ARCHITECTURE.md#41-the-promotion-path).

## What V3 is not

V3 is a research-engine and data-platform evolution. It is **not** a frontend
rewrite: the report concept, the reader-facing memo structure and the evidence-
first presentation established in V2 are preserved. See §52 of the
[implementation plan](IMPLEMENTATION_PLAN.md#6-what-v3-does-not-build) for the
explicit non-goals.
