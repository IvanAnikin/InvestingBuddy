# Open-Web Research: Search Provider Evaluation

**Status:** `PROPOSED — FOR USER REVIEW` (2026-09-29, on `main` = `d29f1e1`). Nothing here is
purchased, configured or keyed.

**Companion documents:** [spec](open-web-research-spec.md) ·
[implementation plan](open-web-research-implementation-plan.md) ·
[threat model](open-web-research-threat-model.md) ·
[acceptance plan](open-web-research-acceptance-plan.md).

**Supersedes nothing yet.** The recommendation below would supersede the "no search provider
needs buying" conclusion of [ADR-048](DECISIONS.md#adr-048-deepseek-web-search-is-the-primary-external-search-path-exa-and-perplexity-are-deferred)
/ [ADR-055](DECISIONS.md#adr-055). It would also narrow the user's 2026-09-05 rule "V3 must
require no new paid SaaS subscriptions"
([PROVIDER_AND_MODEL_STRATEGY §0](v3/PROVIDER_AND_MODEL_STRATEGY.md)). That rule is the
user's to change, so this document ends with a decision gate. It does not make the
decision.

**How the evidence was gathered.**
- Vendor documentation, pricing pages and terms of service were read on 2026-09-29.
- Four claims were re-read first-hand because the recommendation depends on them: DeepSeek's
  Responses API guide, Brave's terms, Exa's pricing and terms, and Tavily's credits page,
  search reference and terms. Each carries the tag **verified 2026-09-29**.
- Everything else was gathered by a research pass and is marked with its source URL. Vendor
  prices change often, so re-read each price page before approving spend.
- **Unverified** means no primary source was read.

---

## 1. What InvestingBuddy needs from a search provider

The provider's only job is to answer "where on the public web should we look?". It is not
trusted for anything else. Every other step (fetch, extract, verify, cite) is done by
InvestingBuddy's own code ([ADR-044](DECISIONS.md#adr-044-external-research-output-enters-as-a-lead-never-as-evidence),
[ADR-056](DECISIONS.md#adr-056-external-research-enters-through-two-tools-and-only-our-own-fetch-may-mint-evidence)).
That gives six requirements, in priority order:

1. **A raw, ranked result list with real URLs.** A synthesised answer with a few citations is
   not enough. Answer-only or "grounding" products are structurally unfit (§3).
2. **Terms that let us keep the audit trail.** The spec requires every lead to record provider,
   query, timestamp, rank, title, snippet and URL (spec §15). **A provider whose terms forbid
   storing results cannot supply that provenance.** On price and features the providers are
   close. On terms they are not, and terms decided this evaluation.
3. **Filters:** domain include/exclude, date range, country and language (for EU local-language
   research), and news vs general.
4. **Real network execution that can be measured.** A request id and usage figure per call, so
   "a search ran" is a fact the platform records, not a guess from an answer's text. This is
   the failure mode that DeepSeek exposed (§2).
5. **Pay-as-you-go pricing without a subscription commitment**, at our volume of
   ~3,000–6,000 queries a month.
6. **A data-use posture compatible with public-context queries only.** Governance rule G1
   (spec §24) keeps private data out of queries entirely, so training on queries is tolerable.
   It still has to be recorded.

Page extraction by the provider is **optional**. InvestingBuddy fetches and extracts pages
itself (spec §9), so a provider's extract or contents endpoint is at most a fallback. It is
never the source of evidence.

---

## 2. The incumbent: DeepSeek `/responses` `web_search` is not a search provider

**Verified 2026-09-29** from <https://api-docs.deepseek.com/guides/responses_api/>. The
compatibility table reads:

> "web_search / file_search / code_interpreter / computer_use / mcp / other built-in tools: **Ignored**"
>
> "Unsupported parameters are silently ignored and do not cause errors"

Four points follow from that:

- **What V3.19 treated as an outage matches documented behaviour.** V3.19 recorded it as an
  outage starting 2026-09-26 ([acceptance report §2.1](v3.19-acceptance-report.md)). It
  should not be planned around as a temporary failure.
- **ADR-055's premise no longer holds.** ADR-055 said the search is real, and ADR-048 made
  DeepSeek the primary search path on that basis.
- **Production is exposed right now.** `V3_DEEPSEEK_SEARCH_ENABLED=true` was last recorded as
  on in production ([v3.16.1b report](v3.16.1b-production-acceptance-report.md)).
  `search_web` → `investigate_with_search` does not force the tool and does not disable
  thinking (`integrations/deepseek/transport.py:720-729`). So the Investigator's "external
  rung" today gets **model recall labelled as a search lead**. Our own fetch still verifies
  every URL, so no unverified claim is promoted. The label is still wrong, and that label
  is the ambiguity the spec's rule "search provenance is a network fact" closes (spec §22.3).
- **DeepSeek stays** as a cheap model for query expansion and entity extraction. It is not a
  search provider and must never be described as one.

---

## 3. Structurally excluded products

| Product | Why excluded |
|---|---|
| **Grounding with Bing Search / Foundry "Web Search" tool** | Returns an answer plus `url_citation` links, never the raw results. The docs say "Developers and end users don't have access to raw content returned from Grounding with Bing Search". The terms forbid "using links for crawling or scraping", so fetching and verifying the links is exactly the prohibited act. Data leaves the Azure compliance boundary, and the DPA does not apply. $14 per 1k tool calls plus model tokens. <https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/bing-tools>, <https://www.microsoft.com/en-us/bing/apis/grounding-legal-enterprise> |
| **Bing Web / News Search API** | Retired 2025-08-11. <https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement> |
| **Grounding with Google Search (Gemini / Vertex)** | Returns citations only. The terms forbid "using Links to identify destination pages for crawling or scraping" and require unmodified Search Suggestions to be shown. <https://ai.google.dev/gemini-api/terms> |
| **Google Custom Search JSON API** | Closed to new customers; existing customers must migrate by 2027-01-01. <https://developers.google.com/custom-search/v1/overview> |
| **SERP scrapers (SerpApi, Serper, SearchAPI.io)** | Google-quality results at low cost ($1–25 per 1k), but Google v. SerpApi is unresolved (amended complaint 2026-08-10, per the research pass; **unverified** first-hand). A platform whose output must be defensible should not depend on it. At most a manual developer aid. |
| **OpenAI `web_search` tool, Anthropic `web_search` / `web_fetch`** | Model-mediated: the model decides what to search and what to report, so neither is a clean `SearchProvider`. OpenAI: $10–25 per 1k plus tokens, no date filter, and live access is not eligible for zero data retention (ZDR). Anthropic: $10 per 1k plus result tokens; its `web_fetch` can fetch only URLs already in the conversation. Both could later serve as a `ResearchProvider` (a contractor) under ADR-045, not as the search index. |
| **Marginalia, Common Crawl** | Marginalia is a small-web index with CC-BY-NC-SA results. Common Crawl (CC-MAIN-2026-39) lags about a month. Neither is live search (§8). |

---

## 4. Candidate comparison

"Raw list" means the API returns a ranked list of URLs. "Storage" summarises what the
standard terms allow us to keep from a response.

| Provider | Raw list | Max results per query | Domain filter | Date filter | Country / language | List price | Free allowance | Storage under standard terms |
|---|---|---|---|---|---|---|---|---|
| **Tavily** | ✅ | 0–20 | include ≤300 (`restrict` or `prefer`), exclude ≤150 | `time_range` d/w/m/y; `start_date`/`end_date`; `filter_by_published_date` | `country` (195+), `language` + `filter_by_language` | 1 credit basic/fast, 2 advanced. Pay-as-you-go $0.008/credit; plans $30 for 4k … $500 for 100k | 1,000 credits/month | **No restriction found** in the ToS (**verified 2026-09-29**) |
| **Exa** | ✅ | 1–100 (Enterprise up to 1,000) | include/exclude up to 1,200 each, with paths and wildcards | published-date range | none found (**unverified**) | Instant $4/1k, Fast/Auto $7/1k, Deep $12–15/1k; +$1/1k results beyond 10; contents $1/1k pages (**verified 2026-09-29**) | $10/month credit | **Restricted:** §4.2(a) forbids copying or storing "any information … obtained from or through the Services" without written permission (**verified 2026-09-29**) |
| **Brave Search API** | ✅ | web 20 (offset ≤9), news 50 | `site:` operator; Goggles for reranking | `freshness` pd/pw/pm/py or a range | `country`, `search_lang` | $5/1k at 50 QPS | $5/month credit (~1k queries) | **Forbidden:** "store, cache, or create a database of Search Results, in whole or in part, other than transient storage"; no AI training or evaluation on results (**verified 2026-09-29**) |
| **Parallel** | ✅ | 10 base | source policy (**unverified**) | **unverified** | **unverified** | Fast/Turbo $1/1k, Basic/Advanced $5/1k | ~5,000 requests/month | ZDR on Enterprise only; storage terms **unverified** |
| **Perplexity Search API** | ✅ | ≤20 (batches of up to 5 queries per call) | allow or deny list ≤20 | after/before, last_updated, recency | `country`, ≤20 languages | $5/1k | none | Search addendum lets Perplexity retain and use search data; excluded from ZDR (addendum page returned 403; seen via search results, **unverified**) |
| **Linkup** (Paris) | ✅ | **unverified** | **unverified** | **unverified** | **unverified** | ~$5–6/1k | ~4,000 queries | EU hosting **unverified** |
| **Mojeek** (UK, own index) | ✅ | **unverified** | full operators | **unverified** | country and language boosting | £2–3/1k | trial | "optional data storage" on Business/Enterprise |
| **You.com** | ✅ | up to 100 | **unverified** | **unverified** | **unverified** | $5/1k; contents $1/1k | 100/day, $100 credit | **unverified** |
| **Kagi** | ✅ | **unverified** | lenses | **unverified** | **unverified** | $12/1k | none | **unverified** |

Sources: <https://docs.tavily.com/documentation/api-credits>,
<https://docs.tavily.com/documentation/api-reference/endpoint/search>,
<https://www.tavily.com/terms>, <https://exa.ai/pricing>, <https://exa.ai/terms>,
<https://exa.ai/docs/reference/search>, <https://brave.com/search/api/>,
<https://api-dashboard.search.brave.com/documentation/resources/terms-of-service>,
<https://parallel.ai/pricing>, <https://docs.perplexity.ai/getting-started/pricing>.

### 4.1 Per-provider notes

**Tavily**
- A raw ranked list, each result with `title`, `url`, `content` (a summary snippet),
  `score`, and optionally `published_date` and `raw_content`.
- Each response has a `request_id` and a `usage` block. Together they give a per-call network
  fact the platform can record (requirement 4).
- `topic=news` is a usable news vertical. There is an Extract endpoint (1 credit per 5 URLs;
  failed extractions are not charged).
- **Terms, verified 2026-09-29:**
  - §9.2 grants Tavily a perpetual licence over Customer Input.
  - §6.5 lets Tavily and its AI sub-processors "use, process, analyze, and retain Customer
    Input … and Outputs … for purposes of training". The ToS offers no opt-out; the research
    pass found a help-centre "Allow Use of Query Data" setting (**unverified**).
  - Neither clause restricts what we store, and our queries are public-context only.
- The research pass reports that Nebius acquired Tavily in 2026 (**unverified**). That is a
  change-of-control risk, and the pluggable interface is the mitigation.

**Exa**
- The strongest thesis-to-company search reviewed.
  - Search types `instant|fast|auto|deep*`.
  - Categories include `company`, `financial report`, `news` and `publication`.
  - Up to 100 results per query.
  - Contents handles PDFs and JavaScript-rendered pages.
- **Terms, verified 2026-09-29:**
  - §4.2(a) forbids copying or storing "any information contained on, or obtained from or
    through, the Services … except … as otherwise expressly permitted … by us in writing".
  - §1.2(c) grants Exa a perpetual licence to "host, cache, store, reproduce … any User Input
    and Output … to provide, develop and improve" its services.
- A persisted list of ranks and snippets is information obtained through the service.
  **Exa is usable only after written permission to keep result provenance**, or under an
  Enterprise agreement, which is also where ZDR lives.
- No country or language parameter was found. That is a weakness for local-language EU
  research.

**Brave**
- The only cheap independent index (it claims 30B+ pages, with 100M+ refreshed daily).
- `filetype:pdf` and `site:` operators work. It has a news endpoint and country and language
  parameters.
- **The terms rule out persisting results beyond transient storage**, and forbid using
  results to "evaluate … or benchmark" AI models. That second clause is itself in tension
  with our provider benchmark harness (`services/providers/benchmark.py`).
- Storage rights require an Enterprise order form.
- Usable only as a transient failover whose results are never persisted. That defeats spec
  §15, so it is **not recommended** unless Enterprise terms are obtained.

**Parallel**
- The cheapest semantic search ($1 per 1k at Fast) with the most generous free tier.
- The filter parameters and storage terms were not verified. A benchmark candidate, not a
  recommendation.

**Perplexity**
- Good filters, and a batch of up to 5 queries counts as one request.
- Search data is excluded from ZDR, and Sonar Chat Completions support ended 2026-09-27 per
  its docs (**unverified** first-hand).

---

## 5. Cost model at InvestingBuddy volume

Volume assumptions come from the budget profiles in spec §19:
- Discovery STANDARD issues ≤24 queries.
- Company STANDARD issues ≤16.
- DEEP issues ≤36.
- A targeted follow-up issues ≤6.

A realistic private-use month is **60 discovery runs + 150 company runs + 100 follow-ups
≈ 60×24 + 150×16 + 100×6 = 4,440 queries**. The worst case, with every run at its cap and
some at DEEP, is ~6,000.

| Provider | Unit price | 4,440 queries/month | 6,000 queries/month | Notes |
|---|---|---|---|---|
| **Tavily**, basic depth, pay-as-you-go | $0.008/credit; first 1,000 credits free | (4,440−1,000)×$0.008 ≈ **$28** | ≈ **$40** | Advanced depth doubles credits. Use it only for DEEP entity discovery (~15% of queries): ≈ +$5 |
| Tavily Project plan | $30 for 4k credits | $30 + 440×$0.008 ≈ **$34** | ≈ **$46** | A subscription; pay-as-you-go needs none |
| **Exa**, Auto | $7/1k; $10 free per month | ≈ **$21** | ≈ **$32** | Instant $4/1k: ≈ $8 / $14. **Blocked on terms (§4.1)** |
| Brave | $5/1k; $5 free per month | ≈ **$17** | ≈ **$25** | **Blocked on terms (§4.1)** |
| Parallel, Fast | $1/1k; ~5k free | ≈ **$0** | ≈ **$1** | Filters and terms unverified |

**Per run (Tavily basic):**

| Run type | Queries | Search cost |
|---|---|---|
| Discovery STANDARD | 24 | $0.19 |
| Company STANDARD | 16 | $0.13 |
| Company DEEP | 36 | $0.29 (≈ $0.35 with a share of advanced queries) |

**Costs outside the provider:**
- LLM query expansion and entity extraction on DeepSeek: a few cents per run.
- The existing Council.
- Optional Document Intelligence OCR for scanned web PDFs: $1.50 per 1k pages (Read) or
  $10 per 1k pages (Layout).
- Egress bandwidth.

The spec's budget ceilings bound every one of these.

---

## 6. Verdicts

| Verdict | Provider | Why |
|---|---|---|
| **BEST TECHNICAL OPTION** | **Exa** | Semantic search suited to thesis → niche-company discovery; `company` and `financial report` categories; up to 100 results; 1,200-domain filters; date filters. It is held back only by terms (§4.1) and the missing locale parameters. |
| **BEST LOW-COST OPTION** | **Tavily, pay-as-you-go** (usable). Brave is cheaper per query but disqualified. | Brave ($5 per 1k) and Parallel Fast ($1 per 1k) cost less per query. Brave's terms forbid the provenance the platform must keep, and Parallel's filters and terms are unverified. At ~$28–40 a month, Tavily is the lowest-cost option that meets all six requirements. |
| **BEST FREE OPTION** | **None is production-grade.** Tavily's free 1,000 credits a month (or Exa's $10) covers development, the live smoke test in phase W1 and the acceptance campaign, not steady private use. | Every free tier still needs an account and an API key, so it still needs **user approval**. |
| **RECOMMENDED** | **Primary: Tavily, basic depth by default, pay-as-you-go without a subscription.** Second adapter, later: **Exa**, admitted only after written confirmation from Exa that persisting result provenance (URL, title, rank, query, timestamp) for internal audit is permitted, and after the benchmark in implementation plan W10 shows it wins on `cost_per_verified_useful_finding`. | It is the only candidate that meets the storage, filter, locale, news and pay-as-you-go requirements at once, with a verified terms position. The `SearchProvider` interface keeps the choice reversible. |

**Change-of-provider triggers.** Re-open this evaluation if any of these happens:
- Tavily changes its terms on output storage or query training.
- Tavily's measured zero-result rate or fetch-survival rate falls below the acceptance plan
  thresholds.
- The change of control materially changes the service.

---

## 7. Decision gate — USER APPROVAL REQUIRED

Nothing below happens without explicit approval.

| # | Decision | What is needed | Cost |
|---|---|---|---|
| P1 | Amend the "no new paid SaaS" rule for **one** search provider | Your decision. This supersedes ADR-048's "no search provider needs buying" and the DeepSeek-search premise of ADR-055. | — |
| P2 | Create a **Tavily** account on the free Researcher plan, and later enable pay-as-you-go with a hard monthly spend limit | You create the account (it is yours, not the agent's). The key goes into the App Service configuration as `TAVILY_API_KEY`, following the existing app-setting practice (Key Vault reference where RBAC allows). Never commit it. | $0 on the free tier; ~$28–40 a month at expected volume with pay-as-you-go |
| P3 | A monthly spend ceiling | Set on the vendor dashboard **and** mirrored as `V3_WEB_SEARCH_MAX_QUERIES_PER_DAY` in the platform, so either side stops a runaway | Proposed: 300 queries/day, $60/month |
| P4 | Record Tavily's data-use terms (training on inputs) as accepted for **public-context queries only** | Your acknowledgement. Governance rule G1 is what makes it acceptable. | — |
| P5 | *(Optional, later)* Ask Exa in writing whether persisting result provenance is permitted | An email from you to Exa. Nothing is built for Exa until the answer is on file. | — |

**What can be built before any approval:**
- W0 (fetch hardening).
- The provider-agnostic contract.
- `FakeSearchProvider` fixtures.
- Provenance persistence.
- The Tavily adapter code, tested against recorded fixtures.

With no key configured, the adapter reports `web_search_unavailable`, which is exactly the
fail-closed state the spec requires. **Live search is the only thing that waits for P1–P4.**

---

## 8. Build vs buy

**Do not build a web search engine.**
- A useful index for this product needs billions of pages refreshed daily, with news freshness
  measured in hours. That means a crawler fleet, politeness infrastructure, storage and a
  ranking stack.
- Common Crawl is free but at best about a month behind, needs an index built on top, and
  still misses the freshness needed for catalysts.
- The platform runs on one App Service B1 at 93–95% memory (`infra/azure/modules/appservice.bicep:66-69`).
  Even a narrow vertical crawler would need infrastructure that does not exist and would
  cost more each month than the provider.

**What InvestingBuddy should build** is the layer no vendor can be trusted with:
- query planning;
- the safe fetcher;
- extraction;
- verification;
- provenance;
- source trust;
- deduplication;
- corpus ingestion.

**Bounded, purpose-specific traversal** (issuer IR sites, government consultation pages) is
already part of the architecture (`services/traversal/issuer_site.py`) and remains ours.
That is crawling of known sites, not search.
