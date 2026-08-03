# Agent Architecture

## Status: Phase 24.1.2 — Press-Release Canonical Link Fix. Company press-release catalyst `source_url` is now the canonical article page (never an image/media URL): `parse_feed` selects the article `<link>` over Atom `rel="enclosure"` image links / `media:content` / `.og.jpg` tiles and keeps the image as `media_url`; `CatalystEvent` gains `media_url` + `source_url_quality`. Underlying Phase 24.1.1 — News Provider Activation + Feed-Status Consistency. Fixes the catalyst feed-status semantics: the press-release provider now tries the discovered feed URL FIRST, applies a lookback filter, and reports a precise `PressReleaseStatus` (`not_discovered` / `feed_discovered_unreadable` / `feed_discovered_no_recent_items` / `feed_discovered_with_items` / `feed_discovered_items_filtered`) so the report never says "no feed found" when a feed was in fact discovered. `discover_catalysts` records per-source `source_statuses` + a `NewsProviderStatus`; `missing_sources` lists `company_press_release` only when genuinely not discovered (not when a discovered feed is merely stale/unreadable), and coverage improves only from usable events. Stale curated feed URLs corrected (Apple `newsroom/rss-feed.rss`, Amazon `aboutamazon.com/news/feed`). The no-key `GdeltNewsProvider` is confirmed end-to-end via `NEWS_PROVIDER_NAME=gdelt` (no key; `NEWS_MAX_RESULTS`/`NEWS_LOOKBACK_DAYS`/`NEWS_TIMEOUT_SECONDS` respected; results T5, mapped T4 only for trusted media). Safety unchanged. Underlying Phase 24.1 — Real News + Company Source Enablement. The `catalyst_discovery_agent` now runs a source-discovery + news-search layer on top of Phase 24. `discover_catalysts` first calls `company_source_discovery_service` (curated verified issuer registry + `profile.website` + SEC/GLEIF sites + optional configured search provider → company website / IR / newsroom / press-release feed, domain-brand verified, social-media/low-quality rejected, never fabricated), builds a bounded recommendation-free `news_query_planner` plan (exact legal name + ticker; company/industry/exchange/primary-source/regulatory groups), runs the SEC (T2) + company press-release (T1, now fed the discovered feed URLs) providers plus a configurable news/search provider (`ConfigurableWebNewsProvider` env-key JSON or no-key `GdeltNewsProvider`, both non-blocking), then `news_relevance_scorer` scores each item 0–1 and splits **company-specific** catalysts from **industry-context** items (industry is never a direct company catalyst — category forced `macro_sector`, direction neutral/mixed). Coverage status is now source-class aware (`filings_only` → `limited`/`adequate`/`strong`). New report sections **Company News Sources** + **Industry Context News**; exchange/listing-venue pages are **T3** (not regulators) and are never promoted to T1/T2; aggregator news stays **T5** unless a trusted-media host maps it to T4. Env: `NEWS_PROVIDER_NAME`/`NEWS_API_KEY`/`NEWS_API_BASE_URL`/`NEWS_MAX_RESULTS`/`NEWS_LOOKBACK_DAYS`/`NEWS_TIMEOUT_SECONDS` (optional, no paid provider needed, no live CI call). Still no recommendations, price targets, fair values, or upside/downside; human review stays required and `safety_valid` stays true; mock unchanged. Underlying Phase 24 — News + Catalyst Discovery. The `company_analysis` workflow gains a `catalyst_discovery_agent` node (after the Investment Committee, before scoring) that runs for `free_real`/`eodhd_free_real` providers only. It calls `discover_catalysts` — `SecRecentFilingsProvider` (recent 8-K/10-Q/10-K/6-K/20-F/DEF 14A/S-registration filings, T2, with 8-K item-number parsing + mapping), a company press-release/IR provider (T1, company-owned primary source, conservative RSS/Atom discovery), and an optional env-gated news provider (T5, `NullNewsProvider` by default — no paid dependency, no live CI call) — then the deterministic `catalyst_classifier` assigns each event a category / direction / strength / evidence-strength / bounded confidence. The catalyst label is **always** `T6_model_estimate`; the underlying evidence keeps its real tier (SEC T2, company press release T1, aggregator T5) and is never promoted. `run_catalyst_agent` emits the report sections (News & Catalyst Discovery, Recent Catalyst Events, SEC Filing Events, Catalyst Evidence Quality, Catalyst Gaps / Next Research Tasks) and weaves catalyst context into Bull/Bear/Risk/Committee/Source-Quality; the Final Report Generator gains a safety-gated `news_catalyst_discovery` section (external headlines neutralised). No recommendations, price targets, fair values, or upside/downside; human review stays required and `safety_valid` stays true. Mock-provider behaviour is unchanged. Underlying Phase 19.4 — Identity + Sector + Market-Metric Enrichment. The `node_build_company_snapshot` step now enriches `free_real` / `eodhd_free_real` snapshots via `company_profile_enrichment` (sector from DB or inferred SEC SIC/T6, industry/website SEC/T2, LEI GLEIF/T2 name-guarded — LEI/ISIN/IPO never fabricated) and `market_metrics_enrichment` (latest close + 52-week range/T5, shares SEC DEI/T2, market cap/EV/P-E as DERIVED ESTIMATES/T6 when inputs exist — EBITDA/EV-EBITDA/beta never fabricated). `FinancialDataAgent` now recognises market cap / EV / P/E as available categories and narrates them as derived estimates; `ValuationGuardAgent` recognises the derived market metrics but keeps every valuation conclusion blocked (readiness stays `partial`). A best-effort GLEIF LEI lookup runs in the snapshot node (non-fatal). Underlying Phase 19.3.1: SEC normalizer selects the latest annual filing across all alias concepts; `investment_committee_chair` emits a canonical `human_review_required`; `BearCaseAgent` / `RiskAgent` acknowledge partial SEC fundamentals. 944 backend tests passing.

---

## Overview

InvestingBuddy uses four teams of specialized LLM agents organized as a council-of-agents system.

```
Research Team
    ↓
Analysis Council
    ↓
Validation & Publishing Team
    ↓
Published Report
    ↓
Judge Team (async, post-publication)
    ↓
Improvement Suggestions → Admin Review → New Prompt Versions
```

---

## Persistence (All Workflows)

Every workflow execution must:
1. Create one `agent_runs` record at start (`status = running`)
2. Create one `agent_steps` record per node with `input_json` and `output_json`
3. Update `agent_runs` at completion (`status = completed` or `failed`)
4. Link any output records (reports, analyses) to `agent_run_id`

This enables debugging, auditing and future judge evaluation.

---

## Implemented Workflows

### company_analysis — Phase 9: Analysis Council MVP

**Trigger:** `POST /api/v1/workflows/company-analysis/run`

**Input:** company UUID (must exist in `companies` table) or ticker + exchange.
Optional: `provider_name` (default: `mock`), `require_schema_valid` (default: `false`),
`use_llm` (default: `false`), `llm_provider` (default: config `LLM_PROVIDER`, default: `mock`).

**Purpose:** 19-node workflow (v6.0.0). Fetches provider data (including SEC EDGAR XBRL fundamentals and EODHD /eod prices via Phase 19.1 providers), builds a structured company snapshot, runs four deterministic Research Team agents, optionally runs an LLM node, stores source + citation records, validates the schema, runs Research Completeness and Citation Validator v2 agents, then runs five deterministic Analysis Council agents (no LLM calls), scores research attractiveness, and saves a draft report. `investment_committee_chair` forces `human_review_required=True` when safety guard triggers. `TrendSignalEngine` is available but not yet wired as a workflow node (Phase 19.2).

> **Phase 28A.1 routing note.** This workflow's "Phase 9 Analysis Council Draft"
> is now an **intermediate** artefact when triggered via the discovery
> **"Run Full Analysis"** flow (`POST /market-discovery/candidates/{id}/run-analysis`).
> That endpoint feeds this workflow's final state into the Phase 28A
> `FinalReportGeneratorService.generate_from_workflow_state`, and the candidate
> links to the resulting **final report** (LLM council when enabled) — not the
> Phase 9 draft. The direct `POST /workflows/company-analysis/run` endpoint still
> returns the Phase 9 draft unchanged. See `docs/API.md` → Phase 28A.1 / 28B.3.

- `use_llm=false` (default): no LLM calls, fully offline, CI-safe.
- `use_llm=true` with `llm_provider=mock`: mock LLM, still offline, no Azure credentials.
- `use_llm=true` with `llm_provider=azure_openai`: calls Azure OpenAI (requires env vars).

**Constraints (absolute):**
- No public BUY/SELL/HOLD/WATCH/REJECT recommendations ever produced.
- No price targets or fair value estimates.
- No personalized investment advice.
- `provisional_internal_status` is admin-only internal workflow state (not a public rating).

**Graph (v6.0.0, 19 nodes):**

```
load_company
    ↓ (company found?)
    ├── No → handle_error → END
    └── Yes → fetch_provider_data       (provider: mock | free_real | eodhd_free_real | eodhd | ...)
                    ↓ (provider valid?)
                    ├── No → handle_error → END
                    └── Yes → create_source_records
                                    ↓
                              build_company_snapshot
                                    ↓
                              financial_data_agent
                                    ↓
                              source_quality_agent
                                    ↓
                              generate_research_sections (skipped if use_llm=False)
                                    ↓
                              create_citations
                                    ↓
                              validate_report_schema
                                    ↓
                              research_completeness_agent
                                    ↓
                              citation_validator_v2
                                    ↓
                              bull_case_agent
                                    ↓
                              bear_case_agent
                                    ↓
                              risk_agent
                                    ↓
                              valuation_guard_agent
                                    ↓
                              investment_committee_chair    (forces human_review_required=True when safety guard triggers)
                                    ↓
                              score_research_attractiveness (Node 17; non-fatal)
                                    ↓
                              save_draft_report
                                    ↓
                              log_agent_steps → END
```

Note: `TrendSignalEngine` is available at `apps/api/app/integrations/trend_signal_engine.py` and is called by `FreeRealSnapshotComposer`, but is not yet a standalone workflow node. Wiring it as Node 18 (between `score_research_attractiveness` and `save_draft_report`) is Phase 19.2.

**Nodes:**

| Node | Agent Name | Step Name | What it does |
|---|---|---|---|
| load_company | WorkflowController | load_company | Creates agent_run; resolves company from DB |
| fetch_provider_data | FinancialDataAgent | fetch_provider_data | Calls FinancialDataService; gets profile + prices |
| create_source_records | SourceRecordAgent | create_source_records | Calls `build_source_record()` + `get_or_create_source()` for each data item |
| build_company_snapshot | SnapshotBuilder | build_company_snapshot | Builds structured snapshot dict; lists missing fields |
| financial_data_agent | FinancialDataResearchAgent | financial_data_agent | Deterministic: lists available vs missing financial data; classifies source tiers; warns on T5/T6. **Phase 19.3:** reads normalized SEC fundamentals from `fundamentals_summary`, marks ~10 financial categories available (revenue, EBIT, net income, assets, total debt, cash, FCF, EPS, ROE, D/E) and narrates revenue/growth/margins/cash-flow/balance-sheet instead of "No financial fundamentals sourced at this phase". **Phase 19.4:** also recognises Phase 19.4 derived `market_cap` / `enterprise_value` / `price_to_earnings` categories and narrates them explicitly as DERIVED ESTIMATES (T6) — no valuation conclusion |
| source_quality_agent | SourceQualityResearchAgent | source_quality_agent | Deterministic: classifies source strength; enforces T5 never promoted; warns on decision-critical T5/T6 claims. **Phase 19.4.1:** the "Obtain LEI from GLEIF API" recommendation is gated on `identity.lei` actually being in `missing_fields`; when market cap / EV are present only as derived T6 estimates it recommends *replacing them with a primary source before publication* — never claiming they are unavailable |
| generate_research_sections | ResearchLLMAgent | generate_research_sections | Calls `ResearchLLMClient.generate_research_sections()`; skipped if `use_llm=False`; non-fatal on error |
| create_citations | CitationAgent | create_citations | Creates Citation records with field_path, source_tier, data_quality |
| validate_report_schema | SchemaValidator | validate_report_schema | Calls `validate_real_asset_report()`; stores ValidationResult |
| research_completeness_agent | ResearchCompletenessAgent | research_completeness_agent | Schema-driven: compares draft against required sections; lists blocking and non-blocking gaps. **Phase 19.4.1:** `_enriched_present_fields()` reads the *enriched* company snapshot so identity `lei`/`isin`/`sector_classification` and `snapshot_financials` market cap / EV / revenue / net income / debt / cash that enrichment already supplied are not reported as missing/blocking, and satisfied identity next-steps (obtain LEI / confirm ISIN) are dropped; genuinely-absent fields (ISIN, EBITDA) stay gaps |
| citation_validator_v2 | CitationValidatorV2 | citation_validator_v2 | Checks DB citations AND schema draft datapoints; flags bare numbers; warns on T5/T6 decision-critical fields |
| bull_case_agent | BullCaseAgent | bull_case_agent | Deterministic: identifies positive thesis points, sector tailwinds, evidence used, assumptions, missing evidence; confidence level based on source tier; safety gate rejects forbidden words |
| bear_case_agent | BearCaseAgent | bear_case_agent | Deterministic: identifies negative thesis points, headwinds, key unknowns; explicitly challenges bull case assumptions; no SELL/SHORT language. **Phase 19.3.1:** when SEC statement fundamentals are sourced but valuation inputs are not, says completeness is *partial* and names only the genuinely missing inputs — no longer claims all fundamentals (revenue/net income/cash flow/debt) are "none sourced" |
| risk_agent | RiskAgent | risk_agent | Deterministic: classifies business/financial/market/regulatory/data-quality/source-quality risks; always includes data quality risks from Phase 8 agents. **Phase 19.3.1:** reports financial data as *partial* (not absent) when SEC statement metrics are present |
| valuation_guard_agent | ValuationGuardAgent | valuation_guard_agent | Deterministic: checks DCF + relative + yield valuation input availability; blocks valuation when mock/T5/T6 or missing fundamentals; lists allowed next steps. **Phase 19.3:** moves `not_ready → partial` when core statement inputs are available from T1/T2, with more specific blockers; every valuation **conclusion** stays blocked (EBITDA / market cap / shares / EV unavailable). **Phase 19.4:** recognises the derived market cap / EV (T6 estimates) in its `partial` blocker wording but still withholds every conclusion — EBITDA, EV/EBITDA and validated market inputs remain absent |
| investment_committee_chair | InvestmentCommitteeChair | investment_committee_chair | Deterministic: synthesises all council outputs; determines provisional_internal_status; sets quality_gate_status; never assigns BUY/SELL/rating |
| catalyst_discovery_agent | CatalystDiscoveryAgent | catalyst_discovery | **Phase 24 / 24.1:** runs for `free_real`/`eodhd_free_real` only (mock unchanged). Calls `discover_catalysts` → company source discovery (curated/`profile.website`/SEC/GLEIF/search → website/IR/newsroom/feed, T1 when verified) + exchange-aware `news_query_planner` + SEC recent filings (T2) + company press releases (T1, fed discovered feeds) + configurable news/search provider (`ConfigurableWebNewsProvider`/`GdeltNewsProvider`, T5/mapped-T4) + industry-context news (separate, `macro_sector`) + `news_relevance_scorer`. Classifies each event (category/direction/strength/evidence, always T6 model label); source-class-aware coverage (`filings_only`→`limited`/`adequate`/`strong`). Via `run_catalyst_agent` produces News & Catalyst Discovery + **Company News Sources** + **Industry Context News** sections + council context. Non-blocking; never emits recommendations, price targets, fair values, or upside/downside |
| save_draft_report | ReportWriter | save_draft_report | Saves draft report; includes Research Team + Analysis Council + Phase 24 catalyst summaries in admin markdown, plus a machine-readable catalyst JSON block for the Final Report Generator |
| log_agent_steps | WorkflowController | log_agent_steps | Marks agent_run completed; logs final step summary |
| handle_error | WorkflowController | handle_error | Marks agent_run failed |

**Sources:**
- `apps/api/app/workflows/company_analysis.py`
- `apps/api/app/agents/research_team/` — Phase 8 agents
- `apps/api/app/agents/analysis_council/` — Phase 9 agents
- `apps/api/app/integrations/llm_provider.py`
- Prompt templates: `packages/prompts/research/phase8_*_v1.md`, `phase9_*_v1.md`

---

### Analysis Council — Phase 9 Agents

**Package:** `apps/api/app/agents/analysis_council/`

All 5 agents are **deterministic** (no LLM calls). All exceptions are caught — the workflow
continues even if an agent fails. Forbidden content (BUY/SELL/price target/fair value) is
detected and either rejected or flagged in warnings.

#### BullCaseAgent (`bull_case_agent.py`)

Identifies the constructive case for further research. **Never** assigns a BUY recommendation
or price target. Output confidence is `"low"` whenever mock/T5/T6 data is active.

Output fields: `positive_thesis_points`, `potential_tailwinds`, `evidence_used`,
`assumptions`, `missing_evidence`, `confidence_level`, `warnings`.

#### BearCaseAgent (`bear_case_agent.py`)

Identifies research risks and challenges bull case assumptions. **Never** uses SELL/SHORT
language. Always lists key unknowns when fundamentals are absent.

Output fields: `negative_thesis_points`, `potential_headwinds`, `key_unknowns`,
`evidence_used`, `missing_evidence`, `confidence_level`, `warnings`.

#### RiskAgent (`risk_agent.py`)

Classifies risks across 6 categories. **Always** includes `data_quality_risks` and
`source_quality_risks` — they are never empty. Unknown items prefixed with `"UNKNOWN:"`.

Output fields: `business_risks`, `financial_risks`, `market_risks`,
`regulatory_geopolitical_risks`, `data_quality_risks`, `source_quality_risks`,
`risk_summary`, `warnings`.

#### ValuationGuardAgent (`valuation_guard_agent.py`)

Gate that blocks premature valuation. Checks DCF inputs (6 required), relative valuation
inputs (4 required), and yield inputs. Sets `valuation_readiness` to `"not_ready"` for
mock/T5/T6 data. Never produces a price target or fair value.

**Phase 19.3 — `partial` readiness:** when core financial-statement inputs (revenue, net
income, free cash flow, total assets, total debt, cash) are available from a **T1/T2** source
and the data is not mock, `valuation_readiness` moves from `not_ready` to `partial`. This
signals "real financial inputs are available" — it does **not** unblock any valuation
conclusion. DCF and relative valuation remain blocked because EBITDA, market capitalization,
shares outstanding and enterprise value are still unavailable (SEC statement data alone does
not contain them, and they are never fabricated). The guard adds a specific blocker explaining
which inputs are present and why no conclusion is produced. Mock and T5/T6-only data stay
`not_ready` (unchanged safety behavior).

Output fields: `valuation_readiness` (`not_ready` | `partial` | `ready`), `available_valuation_inputs`,
`missing_valuation_inputs`, `valuation_blockers`, `allowed_next_steps`,
`disallowed_outputs`, `warnings`.

#### InvestmentCommitteeChair (`investment_committee_chair.py`)

Synthesises all council outputs. Assigns a `provisional_internal_status` from the allowed
set only. **Safety fix (2026-07-12):** forces `human_review_required=True` whenever
the committee safety guard triggers — forbidden terms detected or safety violations fired.
**Review-consistency fix (Phase 19.3.1):** `human_review_required` is now computed
fail-safe — true for mock data, invalid schema, non-`ok` citation status, weak/insufficient
source quality, `not_ready`/`partial` valuation, blocking gaps, or any research-queue status
(every allowed status is one). The `committee_summary` markdown is composed from this final
canonical value (and re-composed after a safety downgrade), so it can never read
"Human review required: False" while the report/page metadata says review is required.

**Allowed internal statuses (admin-only, never public):**
```
research_incomplete
needs_primary_sources
ready_for_deeper_analysis
reject_due_to_data_quality
watchlist_candidate_for_review
```

Output fields: `committee_summary`, `bull_bear_balance`, `primary_open_questions`,
`research_next_steps`, `quality_gate_status`, `provisional_internal_status`,
`human_review_required`, `warnings`.

**Quality gate checks (5 boolean flags):**
- `source_quality_ok` — overall_source_quality is "strong" or "adequate"
- `citation_status_ok` — citation_validator_v2 status is "ok"
- `schema_valid` — schema validation passed
- `valuation_ready` — valuation_readiness is "ready" or "partial"
- `research_complete` — no blocking gaps in research completeness

**Output state fields (Phase 8 + Phase 9 additions):**
```python
{
  # ... (all Phase 7 fields) ...
  # Phase 8: Research Team
  "financial_data_summary": { ... },
  "source_quality_summary": { ... },
  "research_completeness_summary": { ... },
  "upgraded_citation_validation": { ... },
  "research_team_warnings": ["..."],
  "research_team_complete": True,
  # Phase 9: Analysis Council
  "bull_case_summary": {
    "positive_thesis_points": ["..."],
    "potential_tailwinds": ["..."],
    "evidence_used": ["..."],
    "assumptions": ["..."],
    "missing_evidence": ["..."],
    "confidence_level": "low" | "medium" | "high",
    "warnings": ["..."]
  },
  "bear_case_summary": {
    "negative_thesis_points": ["..."],
    "potential_headwinds": ["..."],
    "key_unknowns": ["..."],
    "evidence_used": ["..."],
    "missing_evidence": ["..."],
    "confidence_level": "low" | "medium" | "high",
    "warnings": ["..."]
  },
  "risk_summary": {
    "business_risks": ["..."],
    "financial_risks": ["..."],
    "market_risks": ["..."],
    "regulatory_geopolitical_risks": ["..."],
    "data_quality_risks": ["..."],
    "source_quality_risks": ["..."],
    "risk_summary": "...",
    "warnings": ["..."]
  },
  "valuation_guard_summary": {
    "valuation_readiness": "not_ready" | "partial" | "ready",
    "available_valuation_inputs": ["..."],
    "missing_valuation_inputs": ["..."],
    "valuation_blockers": ["CRITICAL: ..."],
    "allowed_next_steps": ["..."],
    "disallowed_outputs": ["price target", "fair value", ...],
    "warnings": ["..."]
  },
  "committee_chair_summary": {
    "committee_summary": "...",
    "bull_bear_balance": "bull_dominant" | "bear_dominant" | "balanced" | "insufficient_data",
    "primary_open_questions": ["..."],
    "research_next_steps": ["..."],
    "quality_gate_status": {
      "source_quality_ok": False,
      "citation_status_ok": False,
      "schema_valid": False,
      "valuation_ready": False,
      "research_complete": False
    },
    "provisional_internal_status": "research_incomplete",
    "human_review_required": True,
    "warnings": ["..."]
  },
  "analysis_council_warnings": ["...aggregated warnings from all 5 AC agents..."],
  "quality_gate_status": { ... },
  "provisional_internal_status": "research_incomplete",
  "human_review_required": True
}
```

**Company snapshot structure:**
```python
{
  "company_identity": {
    "ticker": "TEST",
    "exchange": "OSE",
    "legal_name": "Acme Nordic AS [MOCK]",
    "country_domicile": "Norway",
    "isin": None,           # None → listed in missing_fields
    "lei": None,
  },
  "provider_metadata": {
    "provider_name": "mock",
    "source_tier": "T6_model_estimate",
    "retrieved_at": "2026-06-20T12:00:00Z",
    "is_mock": True,
    "note": "DEMO DATA — MockFinancialDataProvider."
  },
  "source_tier": "T6_model_estimate",
  "retrieved_at": "2026-06-20T12:00:00Z",
  "is_mock": True,
  "profile": { "reporting_currency": "NOK", "sector": "Industrials", ... },
  "price_history_summary": {
    "available": True,
    "currency": "NOK",
    "data_points_count": 5,
    "date_range": {"start": "2026-01-02", "end": "2026-01-08"},
    "latest_close": 11.15
  },
  "missing_fields": ["identity.isin", "identity.lei", "profile.website"],
  "investment_recommendation": null,    # explicitly null — no recommendation at this phase
  "snapshot_generated_at": "2026-06-22T..."
}
```

---

---

## LLM Provider Abstraction (Phase 7)

**Source:** `apps/api/app/integrations/llm_provider.py`

The `ResearchLLMClient` abstract interface allows swapping LLM backends without
changing the workflow graph. Selection is controlled by `LLM_PROVIDER` config.

### Implementations

| Class | Provider Name | Credentials Required | When Used |
|---|---|---|---|
| `MockResearchLLMClient` | `mock` | None | Default; CI; local dev |
| `AzureOpenAIResearchLLMClient` | `azure_openai` | `AZURE_OPENAI_*` env vars | Staging/production with real keys |

### LLM Output Schema (`ResearchSectionsOutput`)

```python
class ResearchSectionsOutput(BaseModel):
    thesis_summary_draft: str          # 1-3 sentences, factual only
    business_overview_draft: str       # 2-4 sentences, factual only
    missing_information: list[str]     # fields needed for full analysis
    self_critique_limitations: str     # 1-2 sentences on gaps and non-advice status
```

**Fields intentionally absent:** `rating`, `price_target`, `conviction`, `valuation`,
`recommendation`. The schema physically cannot produce investment recommendations.

### Safety Gate (`validate_llm_sections`)

After every LLM call, `validate_llm_sections()` checks for:
- Rating keywords: `BUY`, `SELL`, `HOLD`, `WATCH`, `REJECT`, `SHORTLIST`, `WATCHLIST`
- Price target phrases: `price target`, `target price`, `fair value`, `upside of`

If found, warnings are appended to `llm_section_warnings` in state.
The workflow does NOT crash — output is still stored as draft with warnings for admin review.

### Prompt Template

**Path:** `packages/prompts/research/phase7_company_research_v1.md`

Versioned prompt template (v1). Hard constraints enforced in prompt:
1. No investment rating output
2. No price target or fair value
3. No invented financial numbers — only supplied context
4. JSON output only, matching `ResearchSectionsOutput` schema
5. Explicit self-critique section required
6. Context wrapped in `<company_context>` block with prompt injection mitigations

---

## Analysis Output Schema (Phase 2 Placeholder)

All nodes that produce analysis output follow this schema.
Phase 2 returns `is_placeholder: true`; Phase 3+ nodes will return real LLM output.

```json
{
  "ticker": "VOW3",
  "company_name": "Volkswagen AG",
  "rating": "WATCH",
  "confidence_score": 0.50,
  "risk_score": 0.50,
  "investment_horizon_months": 24,
  "thesis": "...",
  "bull_case": ["..."],
  "bear_case": ["..."],
  "catalysts": ["..."],
  "financial_metrics": {},
  "citations": [],
  "missing_information": ["..."],
  "decision_explanation": "...",
  "generated_at": "2026-06-16T12:00:00Z",
  "is_placeholder": true
}
```

Allowed ratings: `BUY`, `WATCH`, `HOLD`, `SELL`, `REJECT`

---

## Implemented Agents (Phase 3 Skeletons)

### CitationValidator

**Source:** `apps/api/app/agents/validation/citation_validator.py`

A structural (non-LLM) validator that checks whether analysis output claims are covered by citations.

**Input:**
```python
CitationValidatorInput(
    ticker="VOW3",
    analysis_output={ ... },    # analysis JSON from analyze_company node
    citations=[ { ... } ]       # list of Citation dicts
)
```

**Output:**
```python
CitationValidatorOutput(
    status="ok" | "warnings" | "failed",
    missing_citations=[{ "section": "financial_metrics", "description": "..." }],
    approved_claims=["thesis"],
    warnings=["[PLACEHOLDER] ..."],
    is_placeholder=True
)
```

**Required sections checked:** `thesis`, `rating`, `financial_metrics`

**Rules:**
- `is_placeholder=True` → status always `"warnings"` (relaxed requirements for Phase 3)
- Empty `financial_metrics` → warning (not a hard failure)
- Empty `thesis` string → warning
- Thesis not cited → `missing_citations` entry + status `"failed"` (real data only)

**Phase 4 upgrade path:** Replace `_extract_claims()` with a LangChain chain over Azure OpenAI.
The `run_citation_validator()` interface does not need to change.

**Validation is also available as a service:** `citation_service.validate_citations_for_draft()`
used by `POST /api/v1/reports/{id}/validate-citations`.

---

---

## Real-Asset Equity Report Schema Contract (Phase 3.5)

All future company-analysis workflows targeting real-asset companies (energy transition, grid, materials, mining, defense sub-tier, etc.) must produce output that is **schema-valid** against:

```
packages/research-contracts/real_asset_equity/v1/report_schema.json
```

This schema enforces the `datapoint` envelope rule: **every value-bearing fact must include source, date, source tier, and data quality flag.** Bare numbers are a schema violation.

### Datapoint Rule

Every financial metric must be wrapped:

```json
{
  "value": 320.0,
  "unit": "USD_m",
  "as_of": "2026-06-01",
  "source_tier": "T5_api_aggregator",
  "source_name": "EODHD fundamentals",
  "source_url": null,
  "data_quality": "B_single_credible",
  "note": "Converted from SEK at 10.42 SEK/USD on 2026-06-01"
}
```

A bare `"market_cap_usd_m": 320.0` is rejected by the schema validator.

### Source Tiers

| Tier | Label | Examples |
|---|---|---|
| T1 | `T1_primary_filing` | Annual reports, 10-K, NI 43-101, company IR |
| T2 | `T2_regulator_or_gov` | SEC EDGAR, SEDAR+, USGS, IEA, Eurostat |
| T3 | `T3_industry_specialist` | Trade bodies, recognized commodity analysts |
| T4 | `T4_quality_media` | FT, Reuters, Bloomberg News |
| T5 | `T5_api_aggregator` | **EODHD**, Stooq, Alpha Vantage |
| T6 | `T6_model_estimate` | Agent-derived calculation (must show method) |

EODHD is T5. See `docs/DATA_SOURCES.md` for full taxonomy.

### Financial Data Provider Integration (Phase 4)

The `FinancialDataService` is now available for use in agent nodes.
Import and call it from any workflow node to fetch company profile or price data:

```python
from app.integrations.financial_data_service import FinancialDataService

svc = FinancialDataService()           # uses FINANCIAL_DATA_PROVIDER config (default: mock)
profile = await svc.get_company_profile(ticker, exchange)
prices  = await svc.get_price_history(ticker, exchange)
```

Provider output carries full provenance in `meta: ProviderResponseMetadata`:
- `provider_name`, `source_tier`, `retrieved_at`, `is_mock`

Phase 13 update: `FinancialDataService.get_fundamentals(ticker, exchange)` is now live.
When `provider_name=eodhd`, the workflow calls it non-fatally in `node_fetch_provider_data` and
passes `FundamentalsData` to `snapshot_builder`. The snapshot gains a `fundamentals_summary` dict
and the schema draft gains a `snapshot_financials` block with datapoint-wrapped T5 values.
EODHD must remain classified as T5 — see `docs/DATA_SOURCES.md`.

Phase 19.3 update: for `provider_name ∈ {free_real, eodhd_free_real}`, fundamentals come from
**SEC EDGAR XBRL (T2)**, not EODHD. `SecEdgarFundamentalsProvider.get_fundamentals()` merges the
base 10 concepts (`parse_company_facts`) with the normalized metrics from
`sec_fundamentals_normalizer.normalize_company_facts()` (gross/operating income, capex, free cash
flow, cash, total debt, derived margins/ROE/debt-to-equity/YoY growth, plus filing metadata).
`enrich_snapshot_with_free_real()` lands these in `fundamentals_summary` with honest labelling:
statement values in `USD_m`, margins/growth in `%`, annual data **not** mislabelled TTM, and
`ebitda_usd_m` / `market_cap_usd_m` / `enterprise_value_usd_m` explicitly `None` (never fabricated).

### CitationValidator Upgrade Path (Phase 4/5)

In Phase 4, `CitationValidator` will validate both:

1. **Database citations** — existing Phase 3 behaviour: are thesis, rating, and financial_metrics sections cited in the `citations` table?
2. **Report schema datapoint source fields** — new: every `datapoint.source_tier` must be present, and T6 estimates in decision-critical fields must trigger a warning.

The workflow must not allow a final report to proceed if `uncited_claim_scan_passed: false` (from the schema's `self_critique` block) or if the schema validator returns errors.

### Schema Validation Utility

`apps/api/app/services/report_validation_service.py` provides offline validation:

```python
from app.services.report_validation_service import validate_real_asset_report

result = validate_real_asset_report(report_dict)
# result.is_valid  → bool
# result.errors    → list of schema violation messages
# result.warnings  → list of D_weak_or_stale datapoints in critical sections
```

This runs with no external calls and can be used as a workflow gate before saving a draft report.

### Discovery Profile

Future research workflows must populate the `discovery_profile` section, which makes obscurity measurable:

- `entry_path` — how the candidate was found (supply-chain laddering preferred over conventional_screen)
- `supply_chain_distance_from_obvious` — steps removed from the obvious beneficiary (2–3 is the target zone)
- `coverage_metrics` — sell-side count, English news volume, sector mis-tag, disclosure language
- `event_trigger` — the specific event that surfaced the name before consensus (insider buy, permit, contract award)

A `conventional_screen` entry path caps the `underresearched_edge` pillar score at 2/5.

---

## Company Discovery Layer (Phase 14)

The discovery layer is a pre-analysis funnel that screens a defined universe of companies
and produces an internal list of candidates for deeper research. It runs **before** the
`company_analysis` workflow and does **not** produce investment recommendations.

**Sources:**
- `apps/api/app/services/screener.py` — `CompanyScreener` (pure logic, no DB)
- `apps/api/app/services/company_discovery_service.py` — `CompanyDiscoveryService` (DB-aware)
- `apps/api/app/api/v1/discovery.py` — 7 admin/dev-only API endpoints

### CompanyScreener

Deterministic, stateless class. No LLM calls. No network calls in CI.

**Input modes:**
- `provider_name="mock"` (default): reads from `_MOCK_UNIVERSE_BY_THEME` dict; assigns `T6_model_estimate` / `D_weak_or_stale`.
- `provider_name="eodhd"` with `eodhd_search_results` list: parses live EODHD search response; assigns `T5_api_aggregator` / `B_single_credible`; filters non-equity types.

**Supported themes (6):**
`energy_transition`, `electrification_grid`, `defense_security`,
`industrial_resilience`, `real_assets`, `materials_mining`

**Candidate output:** `CandidateInput` dataclass — no `recommendation`, `price_target`, or `fair_value` fields.

**T5 warning (always added for EODHD candidates):**
`"Candidate requires primary-source validation before final analysis."`

**candidate_status state machine:**

| Status | Meaning |
|---|---|
| `candidate_found` | Raw find; minimal data |
| `needs_data` | More data needed |
| `needs_primary_sources` | T5/T6 data only; T1/T2 validation required |
| `ready_for_deeper_analysis` | Sufficient data for company-analysis workflow |
| `rejected_by_screen` | Did not meet screen criteria |
| `error` | Error during processing |

Forbidden statuses (never stored): `BUY`, `SELL`, `HOLD`, `WATCH`

### CompanyDiscoveryService

Async service that wraps `CompanyScreener` and persists results to DB.

**`run_screening(db, run_id, universe, params)`** — creates a `ScreeningRun` record,
calls `CompanyScreener.screen()`, persists `ScreeningCandidate` records, and writes a
summary JSON. The summary note reads: `"Internal research funnel only. No investment recommendation produced."`

**`promote_candidate_to_analysis(db, candidate_id)`** — finds or creates a `Company`
record (via ticker + exchange lookup). Sets `candidate_status = "ready_for_deeper_analysis"`.
Returns `PromoteCandidateResponse` with `company_created` bool and message:
`"No recommendation produced. No publishing performed."`.

Promotion does **not** auto-trigger the `company_analysis` workflow. Admin must call
`POST /api/v1/workflows/company-analysis/run` separately with the promoted company ID.

### Discovery API Endpoints (Admin/Dev Only)

All 7 endpoints are tagged `discovery` and are not public.

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/discovery/universes` | Create screening universe |
| GET | `/api/v1/discovery/universes` | List universes |
| POST | `/api/v1/discovery/universes/{id}/runs` | Start a screening run |
| GET | `/api/v1/discovery/universes/{id}/runs` | List runs for a universe |
| GET | `/api/v1/discovery/runs/{id}` | Get run detail + summary |
| GET | `/api/v1/discovery/runs/{id}/candidates` | List candidates for a run |
| POST | `/api/v1/discovery/candidates/{id}/promote` | Promote candidate to Company |

---

## Research Attractiveness Scoring Layer (Phase 15)

The scoring layer runs **after** the Analysis Council in the `company_analysis` workflow and
**after** screening in the Discovery layer. It produces internal research attractiveness scores
only — never investment recommendations.

**Sources:**
- `apps/api/app/services/scoring_engine.py` — `ScoringEngine` (pure logic, no DB, no LLM)
- `apps/api/app/services/scoring_service.py` — `ScoringService` (DB-aware wrapper)
- `apps/api/app/agents/analysis_council/score_research_attractiveness.py` — LangGraph node
- `apps/api/app/api/v1/scoring.py` — 5 admin/dev-only API endpoints

### ScoringEngine

Deterministic, stateless class. No LLM calls. No network calls.

**10 scoring dimensions (all 0–100 integers):**

| Dimension | Weight | Description |
|---|---|---|
| `source_quality_score` | 20% | T1–T6 tier quality |
| `data_completeness_score` | 18% | Available vs expected data fields |
| `theme_alignment_score` | 15% | Keyword match against 6 investment themes |
| `business_quality_score` | 12% | Identity completeness |
| `financial_strength_score` | 12% | Financial data available |
| `valuation_readiness_score` | 10% | Readiness for future valuation work |
| `growth_context_score` | 8% | Growth indicators in discovery reasons |
| `catalyst_visibility_score` | 5% | Catalysts visible |
| `risk_penalty_score` | -20% | Source/data risk (subtracted) |

**Score caps by data tier:**
- T6/mock: overall score ≤ 30/100
- T5: overall score ≤ 60/100
- T1/T2: full 0–100 range

**Safety gate:** `_check_forbidden_terms()` scans all output text and blocks any forbidden
terms (`BUY`, `SELL`, `HOLD`, `WATCH`, `price target`, `fair value`, etc.) from appearing
in any output field. If triggered, output is downgraded to `internal_status = "not_enough_data"`.

### ValuationReadinessService

Readiness-only classifier. **Never** produces a price target, fair value, or upside estimate.

**States:** `not_ready` | `partial` | `ready_for_basic_multiples` | `ready_for_deeper_valuation`

### ALLOWED_INTERNAL_STATUSES (6 research queue labels)

All statuses are research-queue labels for admin use only — never public recommendations.

| Status | Meaning |
|---|---|
| `not_enough_data` | Insufficient data to score |
| `low_priority_research` | Score too low to prioritise |
| `needs_primary_sources` | T5/T6 only; T1/T2 validation required |
| `ready_for_deeper_analysis` | Sufficient data for analysis workflow |
| `high_priority_for_human_review` | High score; admin should review |
| `reject_due_to_data_quality` | Data quality too poor to proceed |

### score_research_attractiveness (Node 17 in company_analysis workflow)

LangGraph node inserted **between** `investment_committee_chair` (Node 16) and
`save_draft_report` (Node 18). Non-fatal — always returns, never raises.

**Inputs:** All Analysis Council summaries from `_run_holder`
(company_snapshot, financial_data_summary, source_quality_summary, research_completeness_summary,
citation_validation_summary, bull_case_summary, bear_case_summary, risk_summary,
valuation_guard_summary, committee_chair_summary)

**Output:** `research_attractiveness_scorecard` dict persisted into `CompanyAnalysisState`
and stored in `_run_holder`.

**On failure:** Returns fallback dict with `internal_status = "not_enough_data"` and
`overall_score = 0`. Workflow continues normally.

### Workflow Version

`company_analysis` workflow version: **6.0.0** (19 nodes total)

---

## Final Report Generator Layer (Phase 16)

Produces a 19-section structured internal draft report from all Phase 1–15 outputs.
**Never produces public investment advice, recommendations, price targets, or fair values.**
Human admin review is always required.

**Sources:**
- `apps/api/app/services/final_report_generator.py` — `FinalReportGeneratorService` (6 methods)
- `apps/api/app/schemas/final_report.py` — Pydantic schemas + `ALLOWED_INTERNAL_STATUSES`
- `apps/api/app/api/v1/final_reports.py` — 5 admin/dev-only API endpoints
- `packages/prompts/research/phase16_final_report_generator_v1.md` — LLM prompt template (optional)

### FinalReportGeneratorService

**Methods:**
- `generate_from_scorecard(scorecard_id, db)` — entry point from Phase 15 scored candidate
- `generate_from_candidate(candidate_id, db)` — entry point from Phase 14 discovery candidate
- `generate_from_company(company_id, db)` — entry point from company record
- `generate_from_report(report_id, db)` — re-generates from an existing report record
- `validate_final_report(report_id, db)` — re-runs safety + schema validation on existing report
- `regenerate_report_section(report_id, section_name, db, notes)` — rebuilds one named section

All methods are fully offline-testable; LLM use is optional (`use_llm=False` by default).

### Safety Gate (`run_safety_gate`)

Scans all section text for forbidden output terms. Returns `SafetyValidationResult`.

**Forbidden terms:** `BUY`, `SELL`, `HOLD`, `WATCH`, `price target`, `target price`,
`fair value`, `intrinsic value`, `upside of`, `upside percentage`, `guaranteed return`,
`will go up`, `will go down`, `personalized advice`, `tailored recommendation`,
`shortlist_high`, `SHORTLIST_HIGH`

**Exempt field names** (not scanned): `disallowed_outputs`, `blocked_methods`,
`forbidden_terms_found`, `forbidden_terms`, `prohibited_outputs`

**`blocks_approval=True`** when any forbidden term is found. This prevents the report
from advancing through the admin review workflow.

### 19 Report Sections

Every section includes provenance labels on all values: `sourced_fact`, `model_interpretation`,
`missing_data`, `assumption`, `human_review_required`.

| # | Section | Description |
|---|---|---|
| 1 | `admin_disclaimer` | Static INTERNAL ADMIN DRAFT ONLY disclaimer |
| 2 | `executive_summary` | 2–4 sentence company overview; optionally LLM-enriched |
| 3 | `company_identity` | Ticker, exchange, ISIN, LEI, domicile, sector |
| 4 | `discovery_rationale` | Discovery reasons and theme match from Phase 14 |
| 5 | `data_availability_summary` | Available vs missing fields, data tier summary |
| 6 | `financial_snapshot` | Revenue, EBITDA, PE, market cap (T5 EODHD; needs T1 validation) |
| 7 | `internal_scorecard` | Phase 15 overall_score + 10 dimensions |
| 8 | `valuation_readiness` | Phase 15 readiness classifier; no price target produced |
| 9 | `bull_case` | Positive thesis from Analysis Council Bull Case Agent |
| 10 | `bear_case` | Negative thesis from Analysis Council Bear Case Agent |
| 11 | `risk_analysis` | 6-category risk from Risk Agent |
| 12 | `source_quality_review` | T1–T6 source distribution and mock-data flags |
| 13 | `citation_validation_review` | Citation status from Citation Validator v2 |
| 14 | `research_completeness_review` | Blocking and non-blocking research gaps |
| 15 | `missing_information` | Aggregated missing fields from all sources |
| 16 | `committee_chair_summary` | Synthesis from Investment Committee Chair |
| 17 | `workflow_status` | Agent run ID, report status, schema/safety results |
| 18 | `human_review_checklist` | Admin checklist before approval; all items required |
| 19 | `source_citation_appendix` | Full citations list with tier and quality |

---

## Workflow Status

| Workflow | Status | Description |
|---|---|---|
| `company_analysis` | ✅ Phase 22.1 (v6.0.0, 19 nodes) | Provider snapshot → Research Team → optional LLM → citations → schema validation → Analysis Council → scoring → draft report |
| `backtesting` / `judge_evaluation` | ✅ Phase 22 (internal, mock provider) | `BacktestingService` + `ResearchJudgeService`; historical quality assessment; no public recommendations |
| `company_analysis` + trend signals | Phase 19.2 | Wire `TrendSignalEngine` as node; Stooq fallback to EODHD price-only on Azure |
| `news_catalyst_workflow` | Phase 24 | News + catalyst discovery via SEC 8-K + optional news API |
| `market_discovery` | ✅ Phase 25 (internal-only) | `market_discovery_service` + `discovery_scoring_service` rank a bounded universe into an internal research-candidate queue (momentum + catalyst + fundamentals + source-quality + completeness − risk penalty). Internal prioritization only — no recommendations, no price targets; human review required. Reuses `company_analysis` per ticker via `discovery_signal_extractor` |
| `weekly_research` | Future | Scheduled full research pipeline (Azure Functions) |
| `watchlist_monitoring` | Phase 30 | Monitor research theses; trigger re-analysis on significant events |

---

## Planned Agent Teams (Phase 3+)

### Team 1: Research Team

| Agent | Status | Responsibility |
|---|---|---|
| Market Scanner | Phase 5 | Finds candidate companies and themes |
| Financial Data Agent | ✅ Phase 8 (deterministic) | Lists available vs missing financial data; classifies source tiers; warns on T5/T6 |
| Source Quality Agent | ✅ Phase 8 (deterministic) | Classifies source strength T1–T6; warns on T5/T6-only decision-critical claims |
| Research Completeness Agent | ✅ Phase 8 (deterministic) | Schema-driven gap analysis; lists blocking gaps and next research tasks |
| Citation Validator v2 | ✅ Phase 8 (deterministic) | Validates DB citations + schema draft datapoints; flags bare numbers and weak-tier decision-critical fields |
| Filings Agent | Phase 5 | Reads annual/quarterly reports, investor presentations |
| News & Geopolitics Agent | Phase 5 | Analyzes macro, geopolitical and regulatory developments |
| Industry Research Agent | Phase 5 | Builds industry context, peer group |

### Team 2: Analysis Council

| Agent | Responsibility |
|---|---|
| Bull Case Analyst | Positive thesis, catalysts, upside case |
| Bear Case Analyst | Negative thesis, downside risks, thesis-break conditions |
| Valuation Analyst | Relative valuation, DCF, EV/EBITDA, FCF yield |
| Risk Analyst | Financial, geopolitical, regulatory, liquidity risks |
| Catalyst Analyst | Near-term and medium-term catalysts |
| Investment Committee Chair | Synthesizes outputs, resolves disagreements, assigns rating |

### Team 3: Validation & Publishing Team

| Agent | Responsibility |
|---|---|
| Citation Validator | Every claim must have a source, date, currency |
| Fact Consistency Validator | No internal contradictions across sections |
| Report Writer | Full investment memo (admin view) |
| Blog Writer | Public web post version |
| Email Writer | Newsletter draft |

### Team 4: Judge Team (Phase 6)

| Agent | Responsibility |
|---|---|
| LLM-as-Judge Evaluator | Reasoning quality, citation quality, risk coverage |
| Backtesting Evaluator | Compares recommendations vs actual market outcomes |
| Prompt Improvement Recommender | Suggests prompt and workflow changes (admin reviews) |

---

## Adding Real LLM Calls to Phase 2 Skeleton

To wire Azure OpenAI into the Phase 2 workflow:

1. Configure `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT_NAME` in `.env`
2. Add `langchain-openai` to `pyproject.toml`
3. Replace the `_build_placeholder_analysis()` call in `node_analyze_company` with a LangChain chain that invokes Azure OpenAI with structured output
4. Add citation fields to the output
5. Update `model_name` and `tokens_used` in the `complete_agent_step` call

The graph structure, persistence and error handling do not need to change.

See `.claude/skills/langgraph-agents/SKILL.md` for agent output schema requirements.

---

## LLM Council Reliability (Phase 32A Slice 4)

> **Status: implemented on branch `phase-32a-slice4-council-reliability` (`5bbaaf4`)
> — PR open, NOT yet merged / deployed / staging-validated.** Gated by a new
> default-OFF master flag `LLM_COUNCIL_RETRY_ENABLED`; with it off the council is
> byte-for-byte identical to today (one attempt per agent, no retry, no fallback,
> null `committee_label` on chair failure).

The single-company LLM council (`run_council` — 8 agents: `financial_analyst`,
`business_moat`, `catalyst`, `risk_governance`, `source_quality_critic`,
`valuation_guard`, `red_team`, `committee_chair`) runs **strictly sequentially**
and **inline in the HTTP request handler**, so total wall-time must stay under the
~230s Azure App Service gateway timeout. Under Azure `gpt-4.1-mini` TPM limits a
large evidence pack (e.g. AAPL) previously left ~4/8 agents `failed` on a single
429, and the chair (last, no fallback) frequently produced a null
`committee_label`.

**Transient vs permanent classification.** A provider error is duck-typed into
`LLMRateLimitError` (HTTP 429; carries a bounded numeric `retry_after`),
`LLMServerError` (HTTP 5xx) or `LLMTimeoutError` — these three are the ONLY
retryable (transient) errors (`is_transient_llm_error`). Everything else is
PERMANENT and never retried: a schema-invalid completion after the single JSON
repair (`LLMJsonError`), missing provider/credentials (`LLMUnavailableError`), a
generic `LLMError`, and a safety quarantine (which yields a `failed` STATUS, not
an exception). Classification reads only a status code + the exception class name
+ a bounded numeric retry-after — never the message, headers, or URL; nothing is
logged there.

**Bounded retry + reserved critical budget.** When the flag is on, `run_council`
runs an initial single pass (every agent once, in order) then a priority-ordered
retry pass over **only** the transiently-failed agents. Extra attempts are capped
(`llm_council_max_retries` for optional agents; `llm_council_critical_max_retries`
for critical ones), with capped jittered exponential backoff, honoring a capped
provider `retry_after`. The whole council lives under a strict total wall-time
deadline (`llm_council_total_budget_seconds`). CRITICAL agents (`financial_analyst`,
`source_quality_critic`, `red_team`, `committee_chair` — plus `valuation_guard`
only when the pack carries financial evidence) get more attempts, and a wall-time
RESERVE (`llm_council_critical_reserve_seconds`) is held back for the two RESERVED
agents (**`red_team` + `committee_chair`**) so earlier agents draining the shared
budget can never starve the adversarial check or the synthesis. Already-completed
outputs are untouched (no duplicate work, no duplicate citations); a recovered
agent's failed placeholder is REPLACED in place (never appended) and its failure
warning is cleared so `result.warnings` and the completed/failed counts reflect
the FINAL state. Before each chair retry the chair's prompt is rebuilt from the
current (possibly recovered) agent summaries.

**Deterministic committee-chair fallback.** If the LLM chair still does not
complete, a deterministic, non-consensus committee summary is attached
(`chair_fallback_used=true`, `committee_label="insufficient_data"`). It is built
ONLY from already-validated stored council outputs and states no recommendation,
no valuation conclusion, and no numeric price objective; `key_points` is empty so
it carries **no citations**. The failed LLM-chair entry is KEPT in `agents`, so
the completed/failed counts and warnings honestly show the council is **visibly
partial**; the fallback is attached separately and excluded from the is_mock /
recount tallies. It is run through the same `check_and_sanitize` safety/citation
gate as any agent output.

**A partial council stays useful and honest.** Reliability changes execution
ONLY. It never flips `publication_ready` (stays `false`) or `human_review_required`
(stays `true`), never fabricates evidence or consensus, and failed agents still
create no citations. Retry telemetry (`llm_agent_retry` / `llm_agent_retry_skipped`
/ `llm_committee_chair_fallback`) carries SAFE fields only — attempt, agent_name,
error_type, duration_ms, backoff_ms, capped retry_after, counts — never prompts,
completions, evidence, or secrets.
