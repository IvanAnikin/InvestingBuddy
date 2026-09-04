# InvestingBuddy V3 — Provider and Model Strategy

**Status:** V3 TARGET. Pricing verified **2026-09-04** by direct fetch of each
vendor's official pricing page (URLs recorded per section).
**Warning:** vendor pricing and model line-ups change frequently. Every number
here carries its verification date. Re-verify before acting on a cost decision.

---

## 1. Why provider abstraction exists

1. **Capabilities change monthly.** A capability that justifies a vendor today is
   commodity in two quarters.
2. **Prices change, and not uniformly.** DeepSeek bills peak/off-peak. Gemini has
   dated price steps. OpenAI has short/long context tiers plus Batch/Flex/Fast
   multipliers. A cost decision made against one shape can invert under another.
3. **Source quality differs by vendor** in ways that matter more than price:
   which sites a search index actually covers, and whether returned URLs resolve.
4. **Different tasks want different models.** Triaging 200 evidence fragments and
   adjudicating a contradiction are not the same job.
5. **No single vendor should own InvestingBuddy's research domain.** The entity
   master, corpus, facts, period/scope semantics and verification are the
   product. A vendor that could not be swapped in a week has become a dependency
   on someone else's roadmap.

---

## 2. Provider categories

Seven interfaces, because these change independently:

| Interface | Responsibility | Must not |
|---|---|---|
| `ModelProvider` | One prompt in, one structured completion out. | Fetch anything. |
| `ResearchProvider` | Managed multi-step investigation; returns `ResearchProviderResult`. | Write to the record. |
| `SearchProvider` | Query → ranked results with URLs, titles, dates, snippets. | Be bound to one model vendor. |
| `BrowserProvider` | Render a JS-gated page and return DOM/text. | Be the default crawler. |
| `StructuredDataProvider` | Official structured feeds (SEC/XBRL, macro series). | Be replaced by a model's recollection. |
| `TranscriptProvider` | Earnings calls: audio, transcript, speaker, Q&A. | Couple domain models to a vendor schema. |
| `EmbeddingProvider` | Text → vector. | Determine retrieval filters. |

`ModelProvider` extends what already exists: `LLMClient`
(`apps/api/app/services/llm/client.py:149-361`) is already an abstract base with
`complete_json`, a transient/permanent error taxonomy (`client.py:112-121`),
token-usage accounting, and a factory that returns `None` rather than crashing
when a provider is unavailable. V3 adds routing above it and two new vendors
behind it — it does not replace it.

---

## 3. The canonical result contract

```python
@dataclass
class ResearchProviderResult:
    provider: str
    model: str
    task_id: str
    status: Literal["completed", "partial", "failed", "timeout", "cancelled"]
    started_at: datetime
    completed_at: datetime | None
    research_leads: list[ResearchLead]
    claimed_findings: list[ClaimedFinding]
    source_candidates: list[SourceCandidate]
    cited_urls: list[str]
    query_log: list[QueryRecord]
    usage: ConsumptionUnits
    cost: CostEstimate
    warnings: list[str]
    raw_provider_metadata: dict[str, Any]
```

> **A `ResearchProviderResult` is not a research record.** It is the raw output of
> a contractor. Everything in it enters the platform as a `ResearchLead` and must
> survive independent retrieval and verification to become evidence. See
> [DATA_AND_EVIDENCE_ARCHITECTURE.md](DATA_AND_EVIDENCE_ARCHITECTURE.md#41-the-promotion-path).

`raw_provider_metadata` is kept because provider behaviour is itself data — it is
what makes the benchmark reproducible six months later.

---

## 4. Verified pricing (2026-09-04)

### 4.1 Model providers

**OpenAI** — <https://platform.openai.com/docs/pricing>, Standard tier, USD per 1M tokens, short context:

| Model | Input | Cached input | Cache write | Output |
|---|---|---|---|---|
| `gpt-6-astra` | $10.00 | $1.00 | $12.50 | $50.00 |
| `gpt-5.6-sol` | $4.00 | $0.40 | $5.00 | $20.00 |
| `gpt-5.6-terra` | $2.00 | $0.20 | $2.50 | $12.00 |
| `gpt-5.6-luna` | $0.20 | $0.02 | $0.25 | $1.20 |

Long context ≈ 2×. **Batch and Flex = 50%** of Standard. **Fast mode = 2×.**
Regional/data-residency endpoints carry a 10% uplift for models released on or
after 2026-03-05. Tools: web search **$10.00 / 1k calls** plus search content
tokens billed at model rates; file search storage $0.10/GB-day (1 GB free) and
$2.50 / 1k tool calls.

**DeepSeek** — <https://api-docs.deepseek.com/quick_start/pricing>, USD per 1M tokens:

| Model | Input (cache miss) | Input (cache hit) | Output | Concurrency |
|---|---|---|---|---|
| `deepseek-v4-flash` | $0.22 off-peak / $0.44 peak | $0.007 / $0.014 | $0.66 / $1.32 | 2500 |
| `deepseek-v4-pro` | $0.66 / $1.32 | $0.022 / $0.044 | $1.98 / $3.96 | 500 |

Off-peak is half of peak. **Peak = 01:00-04:00 and 06:00-10:00 UTC, Mon-Fri.**
Context 1M, max output 384K. OpenAI-format *and* Anthropic-format base URLs; JSON
output, tool calls and the Responses API are supported — so the existing
`LLMClient` shape ports with minimal adaptation.

**Google Gemini** — <https://ai.google.dev/pricing>, USD per 1M tokens:

| Model | Input | Output | Notes |
|---|---|---|---|
| `gemini-3.8-flash` | $0.75 | $3.75 | Through 2026-12-31; **$1.50 / $7.50 from 2027-01-01**. Caching $0.075 + $0.50 per 1M-token-hour storage. Batch/Flex 50%. |
| `gemini-3.1-pro` | $2.00 | $12.00 | Batch 50%. |

**Grounding with Google Search: 5,000 free requests/month shared across all
Gemini 3.x models, then $14 / 1,000 requests.** That free tier is the single most
interesting number on this page for a low-volume private-use platform.

**Anthropic Claude** — <https://docs.claude.com/en/docs/about-claude/pricing>, USD per MTok:

| Model | Input | Cache hit | Output |
|---|---|---|---|
| Claude Fable 5.1 | $10 | $0.25 | $50 |
| Claude Opus 5 | $5 | $0.50 | $25 |
| Claude Sonnet 5 | $2 | $0.20 | $10 |
| Claude Haiku 4.5 | $1 | $0.10 | $5 |

Batch = 50%. Web search **$10 / 1,000 searches** plus token costs. Managed Agents
add $0.08 per session-hour of runtime.

### 4.2 Search providers

**Exa** — <https://docs.exa.ai/reference/pricing>:

| Endpoint | Price |
|---|---|
| `/search` | **$7 / 1k requests** (≤10 results); +$1 / 1k results beyond 10; AI page summaries $1 / 1k pages |
| Deep Search | $12-15 / 1k requests |
| `/contents` | $1 / 1k pages, per content type |
| `/answer` | $5 / 1k requests |
| `/monitors` | $15 / 1k requests |
| Agent | fixed effort $0.012 (minimal) → $1.00 (xhigh) per run; `auto` metered with a $5 default cap |

New accounts get $20 in credits (~2,800 searches); the free tier adds $10/month.

**Perplexity** — <https://docs.perplexity.ai/getting-started/pricing>:

| Item | Price |
|---|---|
| Search API | **$5.00 / 1k requests** |
| `web_search` tool | $0.0025 / invocation (= $2.50 / 1k) |
| `fetch_url` tool | $0.0005 / invocation (= $0.50 / 1k) |
| `finance_search` tool | $0.005 / invocation |
| Sonar | $1 in / $1 out per 1M + $5/$8/$12 per 1k requests by context size |
| Sonar Pro | $3 / $15 per 1M + $6/$10/$14 per 1k |
| Sonar Deep Research | $2 in / $8 out / $2 citation / $3 reasoning per 1M + $5 per 1k search queries |

---

## 5. What the numbers actually say

Vendor list prices are not comparable until they are applied to *this* workload.
A representative InvestingBuddy council-agent call is roughly **6,000 input /
2,200 output tokens** — derived from `llm_council_evidence_max_chars = 24000`
(≈6k tokens at ~4 chars/token, the same heuristic `token_pacer.estimate_tokens`
uses) and `llm_max_output_tokens = 2200`. Cost per such call:

| Model | $/call | vs cheapest |
|---|---|---|
| `deepseek-v4-flash` (off-peak) | $0.00277 | 1.0× |
| `gpt-5.6-luna` | $0.00384 | 1.4× |
| `deepseek-v4-flash` (peak) | $0.00554 | 2.0× |
| `deepseek-v4-pro` (off-peak) | $0.00832 | 3.0× |
| `gemini-3.8-flash` | $0.01275 | 4.6× |
| `claude-haiku-4-5` | $0.01700 | 6.1× |
| `claude-sonnet-5` | $0.03400 | 12.3× |
| `gpt-5.6-terra` | $0.03840 | 13.9× |
| `gemini-3.1-pro` | $0.03840 | 13.9× |
| `gpt-5.6-sol` | $0.06800 | 24.5× |
| `claude-opus-5` | $0.08500 | 30.7× |
| `gpt-6-astra` | $0.17000 | 61.3× |

Three findings that change the plan:

**(a) DeepSeek's cost advantage over `gpt-5.6-luna` is a scheduling artefact.**
Off-peak it is 1.4× cheaper. At peak it is **30% more expensive**. InvestingBuddy
is a low-volume private-use platform whose runs are user-triggered, so it cannot
choose to research only off-peak. The honest conclusion is that DeepSeek and
`gpt-5.6-luna` are *within noise of each other* on price for this workload, and
therefore the choice must be made on **research quality and data governance**,
not on the headline per-token number. That inverts the usual framing: DeepSeek
has to win on merit, not on being cheap.

**(b) The expensive slots are cheap in absolute terms, so do not over-optimise
them.** A full council is ~8 agent calls. Even routing *every* call to
`gpt-6-astra` costs ~$1.36 per report. Routing the seven analyst calls to a cheap
model and the Chair to `gpt-6-astra` costs ~$0.19. The saving is real but it is
cents; the reason to route cheaply is **throughput and rate-limit headroom**
(Azure OpenAI TPM capacity has been a live constraint — see the 10→60 capacity
raise in Phase 32A), not the bill.

**(c) Search is where the money actually goes at research scale.** One Exa search
($0.007) costs more than one `deepseek-v4-flash` council call ($0.00277). A
DEEP-mode run doing 60 searches and 30 page fetches spends ~$0.45 on retrieval
against ~$0.10 on cheap-model reasoning. **The retrieval budget, not the token
budget, is the one that needs a hard cap.**

And a fourth, on the search vendors specifically: **Perplexity's Search API is
$5/1k against Exa's $7/1k, and its `fetch_url` is $0.0005 against Exa's
`/contents` at $0.001/page.** Perplexity is roughly 30-50% cheaper on both axes.
Exa is still the recommended *first* experiment — see below — but not on price,
and the benchmark must be allowed to overturn it.

---

## 6. Initial experimental configuration

Configurable experiment. **Not permanent architecture.** Every one of these is
one config value away from being changed, and none of them may be named inside a
domain module.

| Slot | First experiment | Rationale |
|---|---|---|
| Search | **Exa** | Neural/semantic retrieval suits "find evidence about X" better than keyword search; native date and domain filtering maps onto the corpus's period/entity filters; `/contents` returns clean text without a separate fetch hop. Chosen despite being ~40% dearer than Perplexity per request, because retrieval *quality* dominates retrieval *price* in the cost-per-verified-finding metric. |
| Bulk researcher | **DeepSeek** (`deepseek-v4-flash`) | 1M context reads a whole filing section without chunking games; OpenAI-format API ports onto the existing `LLMClient`; competitive cost. **Gated on data governance** — see §8. |
| Strong synthesis / Chair | **OpenAI** (`gpt-5.6-sol`, escalating to `gpt-6-astra`) | Hardest tasks only: contradiction adjudication, Chair synthesis, difficult Director planning. Already the incumbent via Azure OpenAI, so the credential and observability path exists. |
| Managed Deep Research | **Gemini** (Deep Research; grounding via Google Search) | 5,000 free grounded search requests/month is materially useful at this platform's volume. Treated strictly as a research contractor. |
| Independent Red Team | **Claude** (Sonnet 5 or Opus 5) | A different vendor from the analyst slots, which is the point of a Red Team. Optional. |

`OPEN DECISION` on every row until the benchmark in §7 runs. The incumbent —
Azure OpenAI, already wired and already the live council backend — remains the
default in `STANDARD` mode until a challenger demonstrably wins.

### 6.1 Consumer subscriptions are not production dependencies

Explicitly forbidden as production architecture: ChatGPT Pro UI automation,
Claude Pro/Max UI, consumer Deep Research sessions, and scraping consumer AI
products. Production uses supported APIs under their terms. Consumer
subscriptions may be used **manually, by a developer, for benchmarking** — that
usage never appears in a code path.

Claude Code is a development tool. It does not become the production backend.

---

## 7. Provider benchmark harness

No provider becomes a default because it is cheapest, and none becomes a default
on vibes. Identical tasks, scored identically.

### 7.1 Tasks

| Issuer | Task |
|---|---|
| MRNA | Current pipeline state; regulatory milestones; competing assets. |
| CFR | China/luxury demand; watches vs jewellery; competitor context. |
| ASML | Demand, capex, export restrictions; semiconductor-cycle evidence. |

These are chosen because each stresses a different weakness: MRNA needs
domain-specific registries, CFR needs Group-vs-segment discipline, ASML needs
non-US reporting plus policy sources.

### 7.2 Recorded per run

`provider` · `model` · `queries` · `URLs` · `source_tiers` · `claims` ·
`verified_claims` · `rejected_claims` · `unsupported_claims` · `cost` ·
`latency` · `errors`

### 7.3 Scored metrics

| Metric | Definition |
|---|---|
| `primary_source_ratio` | Share of cited sources at T1/T2. |
| `verification_survival_rate` | Share of claims that survive independent verification. **The single most diagnostic metric.** |
| `citation_traceability` | Share of cited URLs that resolve *and* contain the claim. |
| `novel_evidence_rate` | Share of evidence not already in the corpus. |
| `industry_specificity` | Share of findings using playbook-required metrics. |
| `latency` | Wall time to usable output. |
| **`cost_per_verified_finding`** | Total cost ÷ findings surviving verification. |

> **`cost_per_verified_finding` is the decisive economic metric.** A provider that
> is 5× cheaper per token and produces 80% unverifiable claims is not cheap — it
> is a verification bill with a model attached. The whole point of measuring
> survival rate is that hallucinated research has a *negative* price: it consumes
> the retrieval budget that would have found the real answer.

### 7.4 Cost discipline while benchmarking

Benchmarks are opt-in, budget-capped, and never run in CI by default. See
[ACCEPTANCE_AND_TEST_STRATEGY.md](ACCEPTANCE_AND_TEST_STRATEGY.md#5-external-api-test-safety).

---

## 8. Data governance per provider

Full policy in
[SECURITY_DATA_GOVERNANCE_AND_LICENSING.md](SECURITY_DATA_GOVERNANCE_AND_LICENSING.md).
The provider-facing summary:

| Content class | Default external-model policy |
|---|---|
| `public_official` (SEC, regulators, government) | Permitted. |
| `public_issuer` (annual reports, IR pages) | Permitted. |
| `public_web` | Permitted, treated as untrusted input. |
| `licensed_private` | **Deny** until the licence is read and a per-provider exception is recorded. |
| `user_private` | **Deny** by default; per-document, per-provider opt-in only. |
| `derived` | Follows the most restrictive input class. |

**DeepSeek specifically is `OPEN DECISION` #4** and is provisionally restricted to
`public_official` / `public_issuer` / `public_web` content until its data-handling
and retention terms are read and recorded. Cheap does not override governance,
and this restriction is enforced at the tool boundary rather than by convention.

---

## 9. Model routing configuration

```
classification_model      → cheap, structured, high volume
cheap_research_model      → bulk investigation over public data
document_reasoning_model  → long-context filing reading
deep_research_provider    → managed multi-step investigation
red_team_model            → adversarial; prefer a different vendor
chair_model               → strongest synthesis
translation_model         → bounded; currently OFF
```

Rules:

1. Domain logic names a **slot**, never a model.
2. Every slot resolves through the provider registry, and an unresolvable slot
   degrades to the deterministic path — exactly as `get_llm_client` already
   returns `None` rather than crashing (`client.py:323-361`).
3. Fake providers remain first-class and stay the only clients the unit suite
   uses.
4. Peak/off-peak, Batch and Flex tiers are **routing inputs**, not hidden vendor
   details: a non-urgent bulk classification job is a legitimate Batch candidate
   at 50% cost, and the router should be able to express that.

---

## 10. Cost and budget architecture

Consumption is tracked in **vendor-neutral units**; money is derived. A price
change is then a config change, and historical runs stay comparable.

```
model_input_tokens        web_search_calls        documents_downloaded
model_output_tokens       url_fetch_calls         pages_parsed
cached_tokens             browser_minutes         ocr_pages
model_calls               provider_research_runs  search_index_queries
elapsed_seconds           estimated_external_cost actual_external_cost_if_available
```

`ResearchBudget` bounds: max external cost · max model calls · max searches · max
documents · max browser minutes · max research iterations · max tokens · max wall
time.

Two design notes:

- **`estimated` vs `actual` cost are separate fields.** `LLMUsage.estimated`
  already exists (`client.py:44-58`) and flags when token counts came from the
  ~4-chars/token heuristic rather than provider metadata. Presenting an estimate
  as an actual is the same class of error as presenting a model claim as
  evidence.
- **The budget is checked before spending, not after.** A run that discovers it is
  over budget after a $2 Deep Research call has not been bounded; it has been
  audited.
