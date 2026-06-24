# Agent Architecture

## Status: Phase 8 — Research Team agents: financial data, source quality, research completeness, citation validator v2

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

### company_analysis — Phase 8: Research Team Agents

**Trigger:** `POST /api/v1/workflows/company-analysis/run`

**Input:** company UUID (must exist in `companies` table) or ticker + exchange.
Optional: `provider_name` (default: `mock`), `require_schema_valid` (default: `false`),
`use_llm` (default: `false`), `llm_provider` (default: config `LLM_PROVIDER`, default: `mock`).

**Purpose:** 13-node workflow. Fetches provider data, builds a structured company snapshot,
runs four deterministic Research Team agents (no LLM calls), optionally runs an LLM node
to generate draft research sections, stores source + citation records, validates against
the real-asset report schema, runs Research Completeness and Citation Validator v2 agents,
and saves a draft report with Research Team summaries.

- `use_llm=false` (default): no LLM calls, fully offline, CI-safe.
- `use_llm=true` with `llm_provider=mock`: mock LLM, still offline, no Azure credentials.
- `use_llm=true` with `llm_provider=azure_openai`: calls Azure OpenAI (requires env vars).

No investment recommendations. No BUY/SELL/WATCH/rating. No price targets.

**Graph (v4.0.0):**

```
load_company
    ↓ (company found?)
    ├── No → handle_error → END
    └── Yes → fetch_provider_data
                    ↓ (provider valid?)
                    ├── No → handle_error → END
                    └── Yes → create_source_records
                                    ↓
                              build_company_snapshot
                                    ↓
                              financial_data_agent       ← NEW (Phase 8)
                                    ↓
                              source_quality_agent       ← NEW (Phase 8)
                                    ↓
                              generate_research_sections (skipped if use_llm=False)
                                    ↓
                              create_citations
                                    ↓
                              validate_report_schema
                                    ↓
                              research_completeness_agent ← NEW (Phase 8)
                                    ↓
                              citation_validator_v2       ← NEW (Phase 8)
                                    ↓
                              save_draft_report
                                    ↓
                              log_agent_steps → END
```

**Nodes:**

| Node | Agent Name | Step Name | What it does |
|---|---|---|---|
| load_company | WorkflowController | load_company | Creates agent_run; resolves company from DB |
| fetch_provider_data | FinancialDataAgent | fetch_provider_data | Calls FinancialDataService; gets profile + prices |
| create_source_records | SourceRecordAgent | create_source_records | Calls `build_source_record()` + `get_or_create_source()` for each data item |
| build_company_snapshot | SnapshotBuilder | build_company_snapshot | Builds structured snapshot dict; lists missing fields |
| financial_data_agent | FinancialDataResearchAgent | financial_data_agent | Deterministic: lists available vs missing financial data; classifies source tiers; warns on T5/T6 |
| source_quality_agent | SourceQualityResearchAgent | source_quality_agent | Deterministic: classifies source strength; enforces T5 never promoted; warns on decision-critical T5/T6 claims |
| generate_research_sections | ResearchLLMAgent | generate_research_sections | Calls `ResearchLLMClient.generate_research_sections()`; skipped if `use_llm=False`; non-fatal on error |
| create_citations | CitationAgent | create_citations | Creates Citation records with field_path, source_tier, data_quality |
| validate_report_schema | SchemaValidator | validate_report_schema | Calls `validate_real_asset_report()`; stores ValidationResult |
| research_completeness_agent | ResearchCompletenessAgent | research_completeness_agent | Schema-driven: compares draft against required sections; lists blocking and non-blocking gaps |
| citation_validator_v2 | CitationValidatorV2 | citation_validator_v2 | Checks DB citations AND schema draft datapoints; flags bare numbers; warns on T5/T6 decision-critical fields |
| save_draft_report | ReportWriter | save_draft_report | Saves draft report; includes Research Team summaries in admin markdown |
| log_agent_steps | WorkflowController | log_agent_steps | Marks agent_run completed; logs final step summary |
| handle_error | WorkflowController | handle_error | Marks agent_run failed |

**Sources:**
- `apps/api/app/workflows/company_analysis.py`
- `apps/api/app/agents/research_team/financial_data_agent.py`
- `apps/api/app/agents/research_team/source_quality_agent.py`
- `apps/api/app/agents/research_team/research_completeness_agent.py`
- `apps/api/app/agents/research_team/citation_validator_v2.py`
- `apps/api/app/integrations/llm_provider.py`
- Prompt templates: `packages/prompts/research/phase8_*_v1.md`

**Output state fields (Phase 8 additions):**
```python
{
  # ... (all Phase 7 fields) ...
  # Phase 8: Research Team
  "financial_data_summary": {
    "available_financial_data": ["identity.legal_name", "price_history.latest_close", ...],
    "missing_financial_data": ["financials.revenue", "financials.ebitda", ...],
    "data_quality_notes": ["Mock provider active ..."],
    "source_tier_summary": {"T6_model_estimate": 1, ...},
    "financial_context_summary": "Acme Nordic AS (TEST) — Industrials ...",
    "warnings": ["Mock provider active: all values are synthetic demo data.", ...]
  },
  "source_quality_summary": {
    "overall_source_quality": "weak" | "adequate" | "strong" | "insufficient",
    "strong_sources": [],
    "weak_sources": ["mock (T6_model_estimate): company identity and profile data"],
    "missing_primary_sources": ["Annual report / 10-K ...", ...],
    "aggregator_only_claims": ["identity.legal_name: sourced only from T6_model_estimate", ...],
    "recommended_source_upgrades": ["Replace mock/T6 data with live provider ...", ...],
    "warnings": ["Mock provider active ...", ...]
  },
  "research_completeness_summary": {
    "complete_sections": [],
    "incomplete_sections": ["report_meta", "identity", "snapshot_financials", ...],
    "missing_required_fields": ["report_meta.schema_version", ...],
    "next_research_tasks": ["Verify legal entity via GLEIF", ...],
    "blocking_gaps": ["Required section absent: report_meta ...", ...],
    "non_blocking_gaps": ["Optional section absent: discovery_profile", ...]
  },
  "upgraded_citation_validation": {
    "status": "ok" | "warnings" | "failed",
    "approved_claims": [],
    "missing_citations": [],
    "weak_citation_warnings": ["Mock provider active: all citation records reference synthetic data.", ...],
    "unsupported_number_warnings": [],
    "source_tier_warnings": []
  },
  "research_team_warnings": ["...aggregated warnings from all 4 agents..."],
  "research_team_complete": True
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

The Financial Data Agent (planned: Phase 5) will use `FinancialDataService` to populate
`snapshot_financials` and `financials_deep` sections of the real-asset report schema.

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

## Planned Workflows (Phase 4+)

| Workflow | Status | Description |
|---|---|---|
| company_analysis | ✅ Phase 8 | Provider snapshot → Research Team agents → LLM draft sections (optional) → citations → schema validation → Research Completeness + Citation v2 → draft report |
| company_analysis (full council) | Phase 5 | Full analysis with Azure OpenAI + real citations; full Research + Analysis Council + Validation teams |
| weekly_research | Phase 5 | Scheduled full research pipeline |
| watchlist_monitoring | Phase 5 | Monitor existing watchlist positions |
| judge_evaluation | Phase 7 | Post-publication quality assessment |

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
