# Agent Architecture

## Status: Phase 32D2 — One Final Reconciled Research State. After ingestion and council completion the final-report generator builds ONE reconciled research state (`app/services/final_research_state.py`) and rebuilds every deterministic human-facing surface from it: data availability, missing information, research completeness, source quality, evidence quality, thin-evidence state, evidence channels, bull/bear/risk, valuation readiness, committee chair and executive summary. The Phase-8/9 agents gain an optional `financial_evidence` keyword (and `primary_facts` on the bull case); omitting it reproduces pre-32D2 behaviour exactly, which is what the genuinely-pre-ingestion workflow-time invocation does. The IDENTITY/PRICE provider tier and the FINANCIAL-EVIDENCE tier are now separate facts and are always named separately. Evidence channels split issuer-primary FACTS (T1) from regulator XBRL FACTS (T2) and aggregator fundamentals (T5). The verified issuer registry is consulted by company-source discovery and the discovery evidence pack, which also distinguishes KNOWN-BUT-NOT-FETCHED from UNKNOWN. See ADR-025..ADR-029. Underlying Phase 24.1.2 — Press-Release Canonical Link Fix. Company press-release catalyst `source_url` is now the canonical article page (never an image/media URL): `parse_feed` selects the article `<link>` over Atom `rel="enclosure"` image links / `media:content` / `.og.jpg` tiles and keeps the image as `media_url`; `CatalystEvent` gains `media_url` + `source_url_quality`. Underlying Phase 24.1.1 — News Provider Activation + Feed-Status Consistency. Fixes the catalyst feed-status semantics: the press-release provider now tries the discovered feed URL FIRST, applies a lookback filter, and reports a precise `PressReleaseStatus` (`not_discovered` / `feed_discovered_unreadable` / `feed_discovered_no_recent_items` / `feed_discovered_with_items` / `feed_discovered_items_filtered`) so the report never says "no feed found" when a feed was in fact discovered. `discover_catalysts` records per-source `source_statuses` + a `NewsProviderStatus`; `missing_sources` lists `company_press_release` only when genuinely not discovered (not when a discovered feed is merely stale/unreadable), and coverage improves only from usable events. Stale curated feed URLs corrected (Apple `newsroom/rss-feed.rss`, Amazon `aboutamazon.com/news/feed`). The no-key `GdeltNewsProvider` is confirmed end-to-end via `NEWS_PROVIDER_NAME=gdelt` (no key; `NEWS_MAX_RESULTS`/`NEWS_LOOKBACK_DAYS`/`NEWS_TIMEOUT_SECONDS` respected; results T5, mapped T4 only for trusted media). Safety unchanged. Underlying Phase 24.1 — Real News + Company Source Enablement. The `catalyst_discovery_agent` now runs a source-discovery + news-search layer on top of Phase 24. `discover_catalysts` first calls `company_source_discovery_service` (curated verified issuer registry + `profile.website` + SEC/GLEIF sites + optional configured search provider → company website / IR / newsroom / press-release feed, domain-brand verified, social-media/low-quality rejected, never fabricated), builds a bounded recommendation-free `news_query_planner` plan (exact legal name + ticker; company/industry/exchange/primary-source/regulatory groups), runs the SEC (T2) + company press-release (T1, now fed the discovered feed URLs) providers plus a configurable news/search provider (`ConfigurableWebNewsProvider` env-key JSON or no-key `GdeltNewsProvider`, both non-blocking), then `news_relevance_scorer` scores each item 0–1 and splits **company-specific** catalysts from **industry-context** items (industry is never a direct company catalyst — category forced `macro_sector`, direction neutral/mixed). Coverage status is now source-class aware (`filings_only` → `limited`/`adequate`/`strong`). New report sections **Company News Sources** + **Industry Context News**; exchange/listing-venue pages are **T3** (not regulators) and are never promoted to T1/T2; aggregator news stays **T5** unless a trusted-media host maps it to T4. Env: `NEWS_PROVIDER_NAME`/`NEWS_API_KEY`/`NEWS_API_BASE_URL`/`NEWS_MAX_RESULTS`/`NEWS_LOOKBACK_DAYS`/`NEWS_TIMEOUT_SECONDS` (optional, no paid provider needed, no live CI call). Still no recommendations, price targets, fair values, or upside/downside; human review stays required and `safety_valid` stays true; mock unchanged. Underlying Phase 24 — News + Catalyst Discovery. The `company_analysis` workflow gains a `catalyst_discovery_agent` node (after the Investment Committee, before scoring) that runs for `free_real`/`eodhd_free_real` providers only. It calls `discover_catalysts` — `SecRecentFilingsProvider` (recent 8-K/10-Q/10-K/6-K/20-F/DEF 14A/S-registration filings, T2, with 8-K item-number parsing + mapping), a company press-release/IR provider (T1, company-owned primary source, conservative RSS/Atom discovery), and an optional env-gated news provider (T5, `NullNewsProvider` by default — no paid dependency, no live CI call) — then the deterministic `catalyst_classifier` assigns each event a category / direction / strength / evidence-strength / bounded confidence. The catalyst label is **always** `T6_model_estimate`; the underlying evidence keeps its real tier (SEC T2, company press release T1, aggregator T5) and is never promoted. `run_catalyst_agent` emits the report sections (News & Catalyst Discovery, Recent Catalyst Events, SEC Filing Events, Catalyst Evidence Quality, Catalyst Gaps / Next Research Tasks) and weaves catalyst context into Bull/Bear/Risk/Committee/Source-Quality; the Final Report Generator gains a safety-gated `news_catalyst_discovery` section (external headlines neutralised). No recommendations, price targets, fair values, or upside/downside; human review stays required and `safety_valid` stays true. Mock-provider behaviour is unchanged. Underlying Phase 19.4 — Identity + Sector + Market-Metric Enrichment. The `node_build_company_snapshot` step now enriches `free_real` / `eodhd_free_real` snapshots via `company_profile_enrichment` (sector from DB or inferred SEC SIC/T6, industry/website SEC/T2, LEI GLEIF/T2 name-guarded — LEI/ISIN/IPO never fabricated) and `market_metrics_enrichment` (latest close + 52-week range/T5, shares SEC DEI/T2, market cap/EV/P-E as DERIVED ESTIMATES/T6 when inputs exist — EBITDA/EV-EBITDA/beta never fabricated). `FinancialDataAgent` now recognises market cap / EV / P/E as available categories and narrates them as derived estimates; `ValuationGuardAgent` recognises the derived market metrics but keeps every valuation conclusion blocked (readiness stays `partial`). A best-effort GLEIF LEI lookup runs in the snapshot node (non-fatal). Underlying Phase 19.3.1: SEC normalizer selects the latest annual filing across all alias concepts; `investment_committee_chair` emits a canonical `human_review_required`; `BearCaseAgent` / `RiskAgent` acknowledge partial SEC fundamentals. 944 backend tests passing.

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
>
> **Product-readiness note (2026-08-22).** Two further changes affect this flow:
>
> 1. `POST /market-discovery/candidates/{id}/run-analysis` is now an **async
>    job** (HTTP 202 + poll `GET /candidates/{id}/analysis-job`) — see ADR-018.
>    The workflow itself is unchanged; only where it runs changed.
> 2. The deterministic **Bull / Bear / Risk / Valuation-Readiness** sections are
>    **rebuilt** by the final-report generator after canonical evidence
>    reconciliation (post-ingestion, post-council), because their workflow-time
>    output predates citations, document ingestion and the council and was
>    contradicting the council's own narrative in the same report. Only sections
>    the workflow actually produced are refreshed, and a summary already
>    carrying forbidden language is never rebuilt (that would launder poisoned
>    state past the safety gate). The original workflow draft is retained in
>    full as `legacy_draft_report_id`. See ADR-019.

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
| risk_agent | RiskAgent | risk_agent | Deterministic: classifies business/financial/market/regulatory/data-quality/source-quality risks; always includes data quality risks from Phase 8 agents. **Phase 19.3.1:** reports financial data as *partial* (not absent) when SEC statement metrics are present. **Phase 32D2:** optional `financial_evidence` — names the categories ACTUALLY sourced and their real tier, and scopes "aggregator data only" to identity/price when the financials are T1/T2 |
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

**Phase 32D2 — `primary_facts` + `financial_evidence` (optional keywords):** this
agent resolved fundamentals from the snapshot ALONE, so it printed "Price trend
analysis requires cross-referencing with fundamentals (not yet sourced)" in a
report whose own financial snapshot rendered a validated T1 revenue figure.
Fundamentals now resolve WITH the council's validated issuer-document facts, and
the low-confidence warning is SCOPED — confidence stays low (most statement lines
are still missing) but the stated reason is accurate: identity/price are
aggregator-tier, the financial facts are not.

Output fields: `positive_thesis_points`, `potential_tailwinds`, `evidence_used`,
`assumptions`, `missing_evidence`, `confidence_level`, `warnings`.

#### BearCaseAgent (`bear_case_agent.py`)

Identifies research risks and challenges bull case assumptions. **Never** uses SELL/SHORT
language. Always lists key unknowns when fundamentals are absent.

**Phase 32D2 — `financial_evidence` (optional keyword):** the "partial
completeness" line named a hardcoded "(revenue, net income, cash flow, balance
sheet)" regardless of what was actually sourced — asserting four categories for
a company that had one. It now names the categories ACTUALLY resolved and their
real tier and source. It also reads the RECONCILED financial-data summary, so
"All 18 core financial fundamental categories are missing (revenue, …)" can no
longer appear beside a sourced revenue figure.

Output fields: `negative_thesis_points`, `potential_headwinds`, `key_unknowns`,
`evidence_used`, `missing_evidence`, `confidence_level`, `warnings`.

#### RiskAgent (`risk_agent.py`)

Classifies risks across 6 categories. **Always** includes `data_quality_risks` and
`source_quality_risks` — they are never empty. Unknown items prefixed with `"UNKNOWN:"`.

**Phase 32D2 — `financial_evidence` (optional keyword):** same treatment as the
bear case — the partial-data line names the real categories/tier — plus the risk
SUMMARY's "Data quality:" label. A single tier cannot describe a report whose
identity is T6 and whose revenue is T1; it now states both scopes rather than
printing the identity tier alone beside a validated filing figure.

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

**Phase 32D2 — `financial_evidence` (optional keyword):** the FINAL reconciled
financial-evidence state (`app/services/final_research_state.py`). Without it the
guard judged "is a primary source behind this?" from
`provider_metadata.source_tier` — the IDENTITY/PRICE provider — so an issuer
whose revenue came from its own T1 annual report but whose identity came from a
T6 fallback was scored `not_ready`, listed `financials.revenue` as a MISSING
valuation input, and was told to "Source T1 primary filings (annual report /
10-K) for revenue" it already had. With it, the primary-source test reads the
FINANCIAL evidence tier, the aggregator blocker is scoped to identity/price, and
the extraction next-step names only the statement lines a filing can actually
close (never "extract price to earnings from the annual report"). Passing `None`
reproduces pre-32D2 behaviour exactly, which is what the workflow-time
invocation (genuinely pre-ingestion) does.

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

**Phase 32D2 — `financial_evidence` (optional keyword):** `needs_primary_sources`
must mean "we have no primary source", not "the identity provider is an
aggregator". A live report carried that label while rendering a validated T1
revenue figure from the issuer's own annual report. The chair is now also
REBUILT by the final-report generator after reconciliation, so its headline
("Source quality: … Valuation readiness: … Provisional status: …") reflects the
same state as the sections it summarises, and the executive summary reprints
that same text rather than a stale copy.

**Phase 32D2 — status vocabulary is MAPPED, not overwritten.** This agent's
labels (`research_incomplete`, `watchlist_candidate_for_review`) are not in the
final report's vocabulary. The unmapped fallback silently rewrote the structured
field to `not_enough_data` while the chair's own prose beside it kept saying
`research_incomplete`. `_map_chair_status` translates explicitly, the prose is
restated to match, and the agent's own label is retained in
`agent_internal_status` for audit.

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

> **Status: ✅ CLOSED + STAGING-VALIDATED (PR #76 → `main` `11ab66b`, 2026-08-04).**
> Closure: `docs/development/closures/phase-32a-slice4.md`. Gated by the
> default-OFF master flag `LLM_COUNCIL_RETRY_ENABLED` (flipped ON on staging and
> **KEPT ON**); with it off the council is
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

## Discovery Council Reliability Parity (Phase 32A Slice 6A)

> **Status: ✅ CLOSED + STAGING-VALIDATED (2026-08-10).** PR **#88** (squash) →
> `main` **`25abc7b`**; hotfix PR **#94** (squash) → `main` **`a1e52a6`**; deployed
> to staging at **`b2aa1be`**. Ships behind the default-OFF flag
> `LLM_DISCOVERY_COUNCIL_RETRY_ENABLED` (flipped ON on staging for validation and
> **KEPT ON**); with it off the discovery council is byte-for-byte identical to its
> prior behaviour (one attempt per agent, no retry, no fallback). Closure:
> `docs/development/closures/phase-32a-slice6a.md`.
>
> **Hotfix #94 (visibility-only, found live on staging):** the deterministic
> fallback fired correctly internally, but `DiscoveryCouncilReviewResponse` never
> declared `chair_fallback_used` / `deterministic_discovery_chair` as fields, so
> Pydantic v2 **silently dropped both** before they reached any API consumer,
> including the admin UI. Fields are now declared.
>
> **Live proof:** on discovery run `6b0700a9-...` under real Azure contention the
> council completed 3/8 agents, reported `run_quality="failed"`, and the
> deterministic fallback fired with an honest synthesis naming exactly which agents
> did and did not complete — no fabricated consensus. **Honest limitation:** the
> *recovery* path (transient failure → retry → success → 8/8) was **not** observed
> live; it is covered by offline tests only.

The discovery-run LLM council (`run_discovery_council` — 8 agents:
`run_coordinator`, `candidate_prioritization`, `novelty_coverage`,
`diversity_anti_convergence`, `evidence_sufficiency`, `risk_gatekeeper`,
`run_red_team`, `discovery_chair`) never received the Slice 4 reliability work
above — it called each agent exactly once with no retry, no backoff, no
wall-budget, and no deterministic fallback. This was a plain parity gap (Slice 4
only touched the single-company council), not an intentional design choice: a
real staging run recently completed only 1/8 discovery-council agents under
Azure rate-limiting in the same session where the company council, protected by
Slice 4, completed 8/8.

**Shared engine.** Slice 4's retry/backoff/wall-budget/deterministic-fallback
machinery was extracted out of `council.py` into a new, agent-shape-agnostic
module `apps/api/app/services/llm/retry_engine.py` (`retry_agent`,
`run_with_retries`, `build_budget_exhausted_output`,
`build_deterministic_synthesis`) with zero imports from either council's
schema module and no hardcoded agent-name literal. `council.py` was refactored
to call into it (behavior-preserving — the company council's public API and
generated text, including the deterministic chair-fallback wording, are
unchanged); `discovery_council.py` was then wired to the same engine.

**Discovery-specific budget.** Unlike the company council (strictly sequential,
inline in the HTTP request, bound by the ~230s Azure gateway timeout), the
discovery council runs as an ASYNC background job
(`market_discovery_service.py::process_discovery_council_by_id`, invoked via
`BackgroundTasks`, polled by the admin UI) with no gateway constraint. It gets
its own, more generous budget: `llm_discovery_council_retry_total_budget_seconds`
defaults to 300s (vs. the company council's 150s) and
`llm_discovery_council_retry_critical_reserve_seconds` defaults to 60s (vs.
45s), reserved for `run_red_team` + `discovery_chair`. `LLMJsonError` (malformed
JSON after the single repair) remains PERMANENT/never-retried in both councils —
unchanged, shared classification in `client.py`.

**Deterministic discovery-chair fallback.** If the LLM discovery chair still
does not complete, a deterministic, non-consensus discovery-run summary is
attached (`chair_fallback_used=true`, `run_quality="failed"`). Built only from
already-validated stored agent outputs; states no recommendation, no candidate
action, no consensus; `candidate_notes`/`run_notes` are empty so it carries no
citations. The failed LLM-chair entry is kept in `agents` so completion stays
honestly visible as partial; the fallback runs through the same
`check_and_sanitize` safety/citation gate as any agent output.

Tests: `apps/api/tests/test_phase32a_slice6a_discovery_council_reliability.py`
(22 tests) covering transient recovery, permanent-error non-retry, retry
exhaustion, no-rerun-of-succeeded-agents, critical-reserve protection, fallback
honesty/safety, 8/8, 5/8, near-total 1/8→7/8 (mirroring the real incident),
complete provider outage, flag-OFF byte-identical regression guards, and
byte-for-byte wording preservation for the shared engine's two callers.

### Hotfix — discovery-council output-token budget (`LLMJsonError` collapse)

> **Status: 🟡 implemented, NOT yet staging-validated.** Backend-only, no
> migration, no flag (the scaled budget is always on for the discovery council).

A fresh manual staging discovery-council run collapsed to **1/8** agents
completed. Unlike the incident that motivated Slice 6A, this was **not** rate
limiting: `run_coordinator`, `candidate_prioritization` and `novelty_coverage`
failed with `LLMJsonError`, which is permanent by design and correctly never
retried.

**Root cause.** Both councils shared the SAME flat `llm_max_output_tokens`
(1200). The single-company council's per-agent JSON is a fixed-size qualitative
shape that does not grow with its input. The discovery council's JSON contract
(`discovery_prompts.JSON_CONTRACT`) requires a `candidate_notes` array with one
entry **per candidate** — and the pack carries up to
`llm_discovery_council_max_candidates` (25) of them. On a realistic run the
reply exceeded 1200 output tokens and was cut off mid-object; `_extract_json`
can recover fence-wrapped or prose-surrounded *complete* JSON but never JSON
with no closing brace, and the one-shot repair reuses the SAME budget, so it
failed identically. The three failing agents were exactly the ones whose role
instructions demand per-candidate enumeration; the aggregate-judgment agents
(`diversity_anti_convergence`, `run_red_team`) were unaffected.

**Fix (discovery council only).** The per-agent output budget is now computed
ONCE per run from the pack's candidate count in
`discovery_council.discovery_max_output_tokens()` and threaded down to every
attempt (initial pass and retries) exactly like `evidence_json` / `evidence_ids`:

```
max_tokens = min(CAP, BASE + PER_CANDIDATE * candidate_count)
```

with `llm_discovery_max_output_tokens_base=1200`,
`..._per_candidate=200`, `..._cap=5000`. The cap comfortably covers the default
25-candidate pack and exists so a raised candidate cap can never make one call
unbounded. `council.py` and `llm_max_output_tokens` are **unchanged** — the
company council keeps the flat value. The computed budget is logged (a number)
on `discovery_council_started`; prompts and completions are still never logged.

**Complementary prompt tightening.** `JSON_CONTRACT` now caps each
`candidate_notes[].rationale` at `<=150 chars` (matching how `summary` was
already capped at `<=600 chars`), and a shared `OUTPUT_DISCIPLINE` block tells
every agent to stay terse, emit at most one note per candidate, and keep
`next_source_tasks` to venues that fit **this run's own jurisdiction** as stated
in the pack's `run_context` (region / country) — which also addresses generic
`SEDAR+` / `ASX` suggestions appearing on clearly European runs. This lowers the
worst-case per-candidate cost; it complements the higher ceiling rather than
replacing it. Nothing about what the council may *output* changed: no
recommendation, rating, price-target or valuation language is added or allowed.

**Not done (deliberately):** `LLMJsonError` stays permanent/never-retried, and
no bracket-balancing "repair truncated JSON" logic was added — reconstructing a
cut-off object is unreliable compared with sizing the budget correctly.

**Bundled with it — initial-pass pacing.** The same failing staging run also
burned the full 300s budget with four agents still failing `LLMRateLimitError`
after retries. Excessive parallelism was ruled out (the initial pass is already
strictly sequential — no `asyncio.gather`), but there was zero pacing between
those sequential calls: eight large requests hit one Azure deployment within
seconds. `retry_engine.run_with_retries` gained an OPTIONAL keyword-only
`initial_pass_delay_seconds` (default `0.0` = OFF, so the company and
field-review councils that share the engine are byte-identical). The discovery
council opts in with `llm_discovery_council_initial_pass_delay_seconds=1.5`,
costing at most `7 × 1.5 = 10.5s` against a 300s budget. The delay is never
taken after the last agent, never when it would cross the deadline, never in the
retry pass (which has its own jittered backoff), and never on the flag-OFF path
(`_run_offline_pass` is untouched and stays byte-identical to pre-Slice-6A).

Tests: `apps/api/tests/test_hotfix_discovery_council_token_budget.py` (17 tests)
and `apps/api/tests/test_hotfix_discovery_council_initial_pass_pacing.py` (8).
A new deterministic `budget_truncated` mode in `fake_discovery_client.py` cuts
its canned reply off whenever the payload does not fit the `max_tokens` it was
actually called with, so the regression is arithmetic, not a mock assertion:
the same 8-candidate run fails with `LLMJsonError` on exactly 3 of 8 agents
under the old flat 1200 budget and reaches 8/8 under the scaled budget.

## Full-Analysis Report Integrity Reconciliation (Phase 32A Slice 6B)

> **Status: ✅ CLOSED + STAGING-VALIDATED (2026-08-10).** PR **#90** (squash) →
> `main` **`d7c8774`**, plus two corrective hotfixes each triggered by a real
> staging failure — **#95** → **`977cb22`** (identity) and **#93** → **`734fac6`**
> (currency) — and a CI-only fix **#92** → **`7f4c985`**. Deployed to staging at
> **`b2aa1be`**. Closure: `docs/development/closures/phase-32a-slice6b.md`.

Nine independently-root-caused report-integration fixes found during an E2E QA
pass on a real staging report (Burberry Group plc, LSE):

- **Company identity.** `run_candidate_analysis` now seeds/upgrades the
  `Company` DB row with `DiscoveryCandidate.legal_name` (a real, sourced
  identity value) in preference to the ticker-like `company_name`, so a
  report's title/legal_name no longer collapses to the bare ticker. The
  existing `is_placeholder_company_name` upgrade guard is unchanged — a
  genuine existing name is never overwritten.
  **Completed by hotfix #95 (`977cb22`), found live:** seeding the DB row was
  not enough, because `_build_company_identity()` in `final_report_generator.py`
  **always** preferred `company_snapshot` over the DB-seeded `company_record` —
  and a snapshot's own `legal_name` can legitimately BE the ticker (a deliberate
  anti-fabrication stub in `free_real_provider.py` for exchanges SEC EDGAR does
  not cover, which exists because "BA.LSE became THE BOEING COMPANY" once really
  happened). The generator now prefers the DB record **only** when the snapshot's
  own name is a provable placeholder (`is_placeholder_company_name()`), never
  otherwise — the anti-fabrication stub keeps winning against guesswork.
- **Discovery lineage.** A `discovery_lineage` block (discovery_run_id,
  candidate rank/score, thesis relevance/match, sourced from the run's own
  `DiscoveryCandidate`/`DiscoveryRun` rows — never inferred from ticker/name
  matching) is threaded into `Report.source_summary_json` and rendered as an
  additive `discovery_lineage` report section, alongside (not replacing) the
  legacy `discovery_rationale` section that stays honestly "not available"
  for reports launched from a `DiscoveryCandidate` (a different model from
  the legacy `ScreeningCandidate` that section was built for).
- **Price-quote currency honesty.** `eodhd_provider`, `eodhd_price_only_provider`,
  and `stooq_provider` no longer hardcode `currency="USD"` on every price
  history — a genuinely unknown quote currency is `None`, rendered as
  `not_sourced` rather than fabricated (CLAUDE.md rule 6). A new
  `exchange_registry.price_quote_currency_for_exchange()` distinguishes the
  price-quote currency (e.g. LSE trades in **GBX**/pence) from the issuer's
  reporting currency (e.g. GBP) — the two are never conflated. The LLM
  narrative prompt now states `"(currency not confirmed)"` instead of
  silently omitting the field and letting the model infer/guess one.
  **Completed by hotfix #93 (`734fac6`), found live:** the mainline fix missed a
  fourth, separate path used by the actual production flow
  (`provider_name="free_real"`) — `FreeRealSnapshot.to_dict()` never threaded
  currency through and `enrich_snapshot_with_free_real()` independently hardcoded
  `"currency": "USD"`; both now follow the same real-value → registry →
  `not_sourced` pattern. **Known, deliberately unfixed (tracked follow-up):** a
  related USD default remains in `market_metrics_enrichment.py`'s derived
  market-cap fields — genuinely separate scope, recorded rather than hidden.
- **Schema-valid staleness.** `committee_chair_summary.quality_gate_status.schema_valid`
  is now refreshed from the same authoritative post-final-assembly validation
  result the existing RC-6 hotfix already uses for `workflow_status` and the
  human-review checklist — closing a gap RC-6 didn't cover.
- **Blocking/missing-count reconciliation.** `blocking_gaps_count`/
  `non_blocking_gaps_count` now read the real `blocking_gaps`/`non_blocking_gaps`
  lists (were reading a key the producer never wrote, silently defaulting to
  0). The financial-agent-scoped `missing_count` is renamed
  `missing_financial_fields_count` (distinct from the whole-report
  `missing_information` union) and no longer falls back to a misleading `0`
  when financial data is genuinely absent.
- **Document-gap message clarity.** A bot-protection/challenge-page fetch
  (e.g. an IR site with active bot protection) now produces a distinct,
  honest gap message from a genuinely successful fetch that found zero
  candidate links — both remain honest, non-fabricating gap states.
- **OCR status text.** The two unconditional "no OCR in this phase" literals
  (stale since Slice 5B.2 shipped real OCR) are replaced with text
  conditioned on the real `primary_document_ocr_enabled` flag and each
  artifact's actual `failure_code` from the existing closed
  `ingestion_status` vocabulary.
- **Source/citation scope labeling.** The pre-council deterministic-draft
  `sources`/`citations` envelope now carries an explicit
  `"scope": "deterministic_pre_council_draft"` marker next to the six
  broader post-council reconciliation counts, removing an apparent (not
  actual) contradiction.

None of these fixes require an Alembic migration — all use existing JSONB
columns or pure Python/display logic.

## Final-Report Regeneration Crash Fix (Phase 32A Slice 6C)

> **Status: ✅ CLOSED + STAGING-VALIDATED (2026-08-10).** PR **#89** (squash) →
> `main` **`89b7f41`** — no hotfix required. Deployed to staging at **`b2aa1be`**.
> No migration, no new flag, no schema/contract change. Closure:
> `docs/development/closures/phase-32a-slice6c.md`.

Regenerating a final report from an **already-completed** report ("Generate
Internal Final Report Draft") failed with `unhashable type: 'dict'`.

**Root cause, reproduced locally rather than guessed:** `generate_from_report`
re-parses an already-final report's
`committee_chair_summary.provisional_internal_status`. On a rendered report that
value is no longer a bare string but a **datapoint dict**
(`{"value": ..., "provenance": ...}`), and that dict hit an unguarded
`status not in ALLOWED_INTERNAL_STATUSES` set-membership check.

**Fix:** a targeted `_coerce_status_value()` helper applied at all **4**
vulnerable sites, normalizing a status to its scalar form before any
set-membership check. A related diagnosability gap was closed at the same time:
all **6** final-report endpoints previously discarded tracebacks (`str(exc)`
only), which is why the crash surfaced as an opaque message with no server-side
stack; they now call `logger.exception()` (structured and secret-free per Phase
27.1D — ids, statuses and exception context only).

**Live proof** on fresh BRBY report `7d8be857-...`: generate → HTTP 201
(`17f150ee-...`; `schema_valid=true`, `safety_valid=true`,
`human_review_required=true`, `publication_ready=false`, 8 council agents
completed / 0 failed), validate → HTTP 200, and a **second regeneration from the
regenerated report itself** → HTTP 201 (`ecf79192-...`) — the exact
double-regeneration shape that previously crashed. No `TypeError` anywhere.
Regeneration never changes the safety posture: `publication_ready` stays `false`
and `human_review_required` stays `true`.

## Primary-Document Ingestion (Phase 32A Slice 5)

> **Status: ✅ CLOSED + STAGING-VALIDATED as a FOUNDATION, with an explicit
> efficacy caveat (Slice 5A: PR #77 → `main` `354a5ba`, 2026-08-04; migration
> `013` applied on staging).** Gated by the default-OFF master flag
> `PRIMARY_DOCUMENT_INGESTION_ENABLED` (flipped ON on staging and **KEPT ON**);
> with it off the connector / council / evidence-pack / persistence paths are
> byte-for-byte unchanged (Phase 29B.2 behaviour). The later Slices 5B.1, 5B.2 and
> 5B.3 build on this. **Phase 32A as a whole is NOT fully closed** — see
> `docs/development/closures/phase-32a-final-status.md`; closure for this slice:
> `docs/development/closures/phase-32a-slice5a.md`.

Slice 5 deepens the Phase 29B.2 extractor so the single-company council can
eventually reason from an issuer's OWN primary documents (annual report /
registration document) with precise citation provenance — not only metadata-only
references. The ingestion runs in the **source-connector phase inside
`maybe_run_council`, BEFORE the council**, under an AGGREGATE wall-budget
(`PRIMARY_DOCUMENT_INGESTION_BUDGET_SECONDS`) so ingestion + the ~150s council
budget stay under the ~230s inline gateway.

**Ingestion hierarchy (least-cost first).** `primary_document_extractor` structures
one already-discovered, allowlisted document: structured (SEC/XBRL, unchanged) →
deepened HTML (stdlib `HTMLParser`: tables + headings/sections, boilerplate
removal) → native PDF (**pdfplumber**: per-page text + table extraction, each item
carrying method / page / table location / confidence + a raw-bytes `content_hash`)
→ OCR fallback. It is bounded (page / excerpt / char / table-size / wall-clock
caps), honest (wrong magic byte / malformed / encrypted → `extraction_failed`;
valid-but-scanned or empty → `metadata_only`; text never fabricated), never raises,
and treats extracted text as UNTRUSTED, inert data (injection markers preserved
verbatim for a downstream prompt-boundary guard, never executed).

**OCR is a NoOp seam only this slice.** `ocr_provider` mirrors the Phase 30A
`TranslationProvider` seam: the only provider shipped returns an empty
`ocr_unavailable` result (never fabricated text), and `get_ocr_provider` returns
it regardless of config. A real Azure Document Intelligence adapter is deferred
(needs resource provisioning + admin sign-off — see `docs/DECISIONS.md` ADR-014),
so scanned / JS-gated issuer PDFs (e.g. some Richemont reports) still degrade
honestly to metadata-only / gaps this slice.

**Borderless multi-year tables (Phase 32D).** `page.extract_tables()` finds tables
from RULING LINES, so it recovers nothing usable from a glossy "Five-year
summary" or a statement page held together purely by whitespace alignment — on
the real Pandora Annual Report 2025 it returned a degenerate one-column artifact
and produced zero candidates, while the same page's text reached the prose path
FLATTENED, its column→year mapping already destroyed. A second, geometry-driven
pass (`financial_table_reconstructor`) therefore rebuilds such a grid from the
page's positioned words: rows clustered by `top`; header rows carrying ≥ 2 period
tokens; those tokens split into one column group per PHYSICAL table (two tables
printed side by side never share a header map); a group qualified only on uniform
column pitch, distinct strictly-monotonic periods and a clean header band; x-bands
midway between header centres; each numeric word assigned to the band containing
its own centre, clear of both edges. It decides LAYOUT ONLY and emits the same
header-first grid the validator already consumes. Everything ambiguous is refused
with a machine-readable reason, and a period form `ExtractedFact.period` cannot
represent losslessly (interim `H1 2026`, split-year `2025/26`) is detected,
surfaced as a source gap and deliberately NOT promoted. See ADR-030.

**Stricter fact validation.** `extracted_fact_validator` holds table/OCR-derived
values to a higher bar — label/value/unit/period + table-column alignment +
cross-field arithmetic (subtotals) + cross-method agreement, with OCR downgraded.
Only a fact that clears the bar is `validated`; everything short is retained
`excerpt_only` (`rejected` when it fails a hard check) and is never a structured
fact. Two candidates may only be judged to CONTRADICT each other when BOTH are
fully qualified — a candidate whose currency/scale/period could not be
established has unknown units, so comparing its bare digits against a fully
specified figure is a category error. A prose candidate that is a DEGRADED READ
of a page whose table was reconstructed (same label, page and scope) is
superseded by it rather than allowed to conflict: the two are one printed table
read twice, not independent corroboration. Metadata-only references never become facts or claim-verification. Every
extracted fact is `needs_human_review=True`.

**Evidence pack + citations.** When the master flag is on, the evidence budgeter
adds a `primary_document` floor + cap WITHOUT weakening the Slice-2
`financial_floor=3` / news caps. Citations carry the fact's page / section / table
location; a failed / metadata-only extraction yields no citation; OCR provenance
is disclosed. A deep-extracted item carries a runtime-only `document_content_hash`
(excluded from serialization) so the citation write keys one canonical `Source`
per distinct document (raw-bytes identity).

**Persistence + reuse (both flags on).** When BOTH
`PRIMARY_DOCUMENT_INGESTION_ENABLED` and `REPORT_CITATION_PERSISTENCE_ENABLED` are
on, `run_council` threads the deep artifacts (`primary_document_artifacts`,
runtime-only) to the report-write path, which persists `ExtractedDocument` /
`ExtractedFact` rows (migration `013`) next to the citation write, deduped by raw
`content_hash`. Before ingestion runs, a bounded reuse lookup rebuilds a fresh
persisted document for the same company within `PRIMARY_DOCUMENT_REUSE_TTL_HOURS`
(from its stored excerpts + validated facts) so a report regeneration skips the
re-fetch / re-extract. With either flag off there is no reuse lookup and no
persistence — the path is byte-identical.

**Security posture.** No new public endpoint and no user-supplied-URL surface;
every fetch routes through the allowlist-gated hardened layer. Bounded by size /
page / OCR-page caps, a %PDF magic-byte check, a decompression-bomb guard, a
Pillow image-pixel cap, and an opt-in resolved-IP / DNS-rebinding guard (before &
after redirects on the deep path). No JS / browser / paywall / auth bypass.
Logging is counts / status only — never document bytes or extracted text.

## Deep Field Review (Phase 32A Slice 6D)

> **Status: ✅ CLOSED + STAGING-VALIDATED (2026-08-10).** PR **#91** (squash) →
> `main` **`dee5998`**; hotfix PR **#96** (squash) → `main` **`b2aa1be`**;
> migration **`015` applied + schema-verified on staging** (`alembic current` =
> `015`, head); deployed to staging at **`b2aa1be`**. Ships **default-OFF** behind
> `LLM_FIELD_REVIEW_COUNCIL_ENABLED` (flipped ON on staging for validation and
> **KEPT ON**) and, like every council, the shared `LLM_COUNCIL_ENABLED` gate. With
> either flag off no LLM call is made, no fake output is produced in production,
> and migration `015`'s two tables stay empty. Closure:
> `docs/development/closures/phase-32a-slice6d.md`.
>
> **Hotfix #96, found on the first-ever live run:** `field_chair` had **no
> deterministic fallback at all** (unlike the company council/Slice 4 and the
> discovery council/Slice 6A), so when it failed all three priority buckets
> silently stayed empty with no explanation. The fallback described below was
> added, and `chair_fallback_used` / `deterministic_field_chair` were threaded
> storage → API → UI (`FieldReviewResponse.from_row()` uses explicit field reads,
> not a spread, so the explicit read lines were added and **proven necessary** via
> a remove-and-watch-the-test-fail check).

### Why a third council — Discovery Council vs. Deep Field Review

The codebase has **three** distinct councils. They answer different questions and
must never be conflated in code, API, UI, or docs:

| Council | Scope | Runs | Input | Output |
|---|---|---|---|---|
| **Discovery Council** (28B) | one discovery run's **candidate list** | **before** any full analysis exists | shallow candidate signals | run-level review of the candidate list |
| **Company Council** (28A) | **one** company | during that company's analysis | that company's evidence pack | that company's committee-chair verdict |
| **Deep Field Review** (32A/6D) | **several** companies from ONE run | **after** 2+ of them already have a **completed** full analysis | those companies' **already-persisted reports** | internal research-priority shortlist across those companies |

The distinction that matters most in practice is **Discovery Council vs. Deep
Field Review**, because both operate at the level of a discovery run:

- The **Discovery Council** is **shallow and upstream**. It triages a candidate
  list *before* any deep evidence exists, using only the discovery signals
  gathered during the run. It cannot compare companies on financials, primary
  documents or council verdicts, because none of those exist yet.
- The **Deep Field Review** is **deep and downstream**. It runs only once at least
  `FIELD_REVIEW_MIN_CANDIDATES` (default `2`) candidates from that same run have a
  **completed, schema-valid full analysis**, and it reads exclusively from those
  already-persisted reports. It **never re-analyses, re-fetches, or recomputes**
  anything, and it never re-runs a company-council agent.

It fills the gap between the other two: a **comparative** review that reads
completed deep analyses and answers "which company deserves the next unit of
research effort, given the evidence already gathered". It is a **prioritization**
of internal research effort — not a rating, not advice, and never published.

The two are kept visibly separate on every surface: distinct DB tables
(`field_review_runs` / `field_review_candidate_summaries` vs. the discovery-council
payload), distinct endpoints, distinct flags
(`LLM_FIELD_REVIEW_COUNCIL_ENABLED` vs. `LLM_DISCOVERY_COUNCIL_ENABLED`), and a
distinctly labelled and styled admin panel sitting
below — never merged into — the Discovery Council panel.

### Input resolution (`field_review_service.resolve_field_candidates`)

Candidates are read for **one** `discovery_run_id`, ordered by `rank` ascending
with NULLs last, and each is classified:

| Condition | Outcome |
|---|---|
| `analysis_report_id IS NULL` | excluded, `no_analysis_run` |
| report row missing | excluded, `report_deleted` |
| `final_report_version IS NULL` | excluded, `draft_only` |
| schema validation not passed | excluded, `not_schema_valid` |
| beyond `LLM_FIELD_REVIEW_COUNCIL_MAX_COMPANIES` | excluded, `over_company_cap` |
| otherwise | **included**, assigned a stable citation id `F1`, `F2`, … |

Three rules make this safe:

1. **`analysis_report_id` is the ONLY linkage.** There is deliberately no
   "latest report for this company_id" fallback — that would resurrect the
   from-company scoping bug already fixed in Phase 32A and could silently
   substitute a report generated for a *different* run of the same company.
2. **Nothing is silently dropped.** Every excluded candidate is persisted with
   its closed-vocabulary reason and surfaced as a citeable run fact.
3. **Mock / unknown provenance is included WITH a caveat**, never excluded and
   never presented as real.

Below `FIELD_REVIEW_MIN_CANDIDATES` (default `2`) comparable candidates the
service raises `InsufficientAnalyzedCandidatesError` (→ **422**, listing what
exists and why each candidate is not comparable) and the council never runs.

### The bounded comparative pack

`field_review_evidence_pack.build_company_summary` builds one
`FieldReviewCompanySummary` per included report from persisted data ONLY,
reusing the existing `_extract_from_report_content` parser (no second,
drifting markdown/JSON parser) plus `PrimaryDocumentSummary` from
`primary_document_view_service`. It carries identity, discovery relevance (read
verbatim off the candidate row), financial facts **with their own provenance**,
primary-document coverage, evidence/source quality + tiers, business/moat,
catalyst, and risk notes, the **qualitative** `valuation_readiness` label only
(never a number), the company council's stored chair verdict and the stored
`financial_analyst` / `source_quality_critic` / `red_team` summaries (read-only —
no company-council agent is re-run), unresolved gaps, research completeness,
council completion (`agents_completed` / `agents_failed` / `chair_fallback_used`),
`data_provenance`, and honest machine-generated `caveats`. **Every list-valued
sub-field is capped**; a field with no persisted source stays absent rather than
being guessed.

### The eight comparative agents

`comparative_financial_quality` → `thematic_relevance_materiality` →
`comparative_business_quality_moat` → `comparative_catalysts` →
`comparative_risk` → `comparative_evidence_source_quality` → `field_red_team` →
`field_chair` (last).

Non-chair agents receive the full set of company summaries and return per-company
notes (`company_ref` = `F#`, cited rationale, confidence). `field_red_team`
additionally receives the prior agents' already-safety-scanned summaries and
challenges overconfident or unsupported convergence. `field_chair` receives
everything and returns the verdict.

Every agent's output passes through `field_review_citation_checker`: a safety
hit or a citation id outside the pack **quarantines** the agent
(`status=failed`) — it is never sanitized-and-passed. The chair's verdict is
additionally checked for ungrounded and duplicate placements (a company can
appear in at most one bucket).

### Field chair verdict — prioritization, not advice

Three buckets **only**: `strongest_candidates`, `second_tier`,
`blocked_insufficient_evidence`. They mean "research this next", "research this
after the first group", and "cannot be compared yet — evidence too thin". Every
entry cites `citation_ids`, and a company's own caveats are always merged into
its entry so a mock-provenance company can never be presented as clean. Plus
`field_uncertainties` and `field_quality`
(`strong` | `adequate` | `thin` | `failed`).

No rating or action vocabulary exists anywhere in the schema. The stored payload
is re-scanned by the shared `safety_terms` scanner before persistence as a
backstop; a hit forces `safety_valid=false` rather than silently stripping.

### Bounded execution

The review runs as an **async background job** (`start_field_review` →
`process_field_review_task` → `process_field_review_by_id`), mirroring the
discovery council's fresh-session worker pattern: idempotent start (no duplicate
in-flight jobs; a completed review is returned unless `force=true`), a primitive
id handed to `BackgroundTasks`, and a terminal DB row written on **every** path
including an unhandled exception — a job can never stick in `running`.

Retries are strictly bounded and reuse `client.py`'s transient-error
classification (429 / 5xx / timeout only; a quarantine is permanent): an initial
pass under a total wall-time deadline
(`LLM_FIELD_REVIEW_COUNCIL_TOTAL_BUDGET_SECONDS`, default `600` — larger than the
inline single-company council because this is a background job), then a
priority-ordered retry pass with per-agent attempt caps, a capped honored
provider `retry-after`, capped jittered exponential backoff, and a reserve
(`..._CRITICAL_RESERVE_SECONDS`) protecting `field_red_team` + `field_chair`.
`clock` / `sleeper` / `rng` are injectable so tests drive the budget
deterministically. There is no unbounded loop anywhere.

**Deterministic field-chair fallback.** If the LLM `field_chair` still does not
complete, a deterministic, non-consensus field summary is attached
(`chair_fallback_used=true`, `deterministic_field_chair`,
`field_quality="failed"`). All **three** priority buckets stay **empty** — the
fallback never fabricates a ranking, and it deliberately does not push companies
into `blocked_insufficient_evidence`, which asserts something about *that
company's own* evidence rather than about the chair. `field_uncertainties`
states plainly that no ranking was produced, names which comparative agents did
and did not complete (the completed ones' summaries remain usable), and requires
human review. The failed LLM-chair entry is kept in `agents` / `agent_outputs`
so completion stays honestly visible as partial; the fallback runs through the
same `check_and_sanitize` safety/citation gate as any agent output. Mirrors the
company council (Slice 4) and the discovery council (Slice 6A).

### Persistence + surface

Results persist to `field_review_runs` + `field_review_candidate_summaries`
(migration `015`). Admin API: `POST` / `GET
/api/v1/discovery-runs/{run_id}/field-review`. Admin UI: a **"Deep Field
Review"** panel directly below the Discovery Council panel on
`/admin/discovery`, deliberately labelled and styled distinctly, with the run
button disabled (with an explanatory tooltip) until at least two candidates have
a completed full analysis.

Logging is structured and safe (Phase 27.1D): ids, statuses, counts, durations,
capped backoff — never prompts, completions, report bodies, pack text, or
credentials.
