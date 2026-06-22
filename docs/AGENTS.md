# Agent Architecture

## Status: Phase 6 — Real company snapshot workflow; provider data → source records → citations → schema validation

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

### company_analysis — Phase 6: Real Company Snapshot

**Trigger:** `POST /api/v1/workflows/company-analysis/run`

**Input:** company UUID (must exist in `companies` table) or ticker + exchange.
Optional: `provider_name` (default: `mock`), `require_schema_valid` (default: `false`).

**Purpose:** Fetches real provider data, builds a structured company snapshot,
stores source + citation records with provenance, validates the output against the
real-asset equity report schema, and saves a draft report. No LLM calls. No investment
recommendations. Schema validation result is stored regardless of pass/fail.

**Graph:**

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
                              create_citations
                                    ↓
                              validate_report_schema
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
| create_citations | CitationAgent | create_citations | Creates Citation records with field_path, source_tier, data_quality |
| validate_report_schema | SchemaValidator | validate_report_schema | Calls `validate_real_asset_report()`; stores ValidationResult |
| save_draft_report | ReportWriter | save_draft_report | Saves draft report with snapshot JSON, mock/live flag, schema validation status |
| log_agent_steps | WorkflowController | log_agent_steps | Marks agent_run completed; logs final step summary |
| handle_error | WorkflowController | handle_error | Marks agent_run failed |

**Source:** `apps/api/app/workflows/company_analysis.py`
**Snapshot builder:** `apps/api/app/workflows/snapshot_builder.py`

**Output state fields:**
```python
{
  "agent_run_id": "uuid",
  "company_name": "Acme Nordic AS",
  "ticker": "TEST",
  "provider_name": "mock",
  "is_mock": True,
  "company_snapshot": { ... },              # structured snapshot dict
  "source_ids": ["uuid", "uuid"],           # Source records created
  "provider_source_id": "uuid",            # Source UUID for company profile
  "price_source_id": "uuid",               # Source UUID for price data (None if unavailable)
  "citation_ids": ["uuid", ...],           # Citation records with field_path
  "schema_validation_result": {
    "is_valid": False,
    "errors": ["..."],
    "warnings": []
  },
  "schema_valid": False,
  "draft_report_id": "uuid",
  "analysis_output": { "is_placeholder": True, ... },
  "status": "completed" | "failed",
  "error": None | "error message"
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
| company_analysis | ✅ Phase 6 | Provider data snapshot → sources + citations (with field_path) → schema validation → draft report |
| company_analysis (real LLM) | Phase 5 | Full analysis with Azure OpenAI + real citations; replace placeholder nodes with LangChain chains |
| weekly_research | Phase 5 | Scheduled full research pipeline |
| watchlist_monitoring | Phase 5 | Monitor existing watchlist positions |
| judge_evaluation | Phase 7 | Post-publication quality assessment |

---

## Planned Agent Teams (Phase 3+)

### Team 1: Research Team

| Agent | Responsibility |
|---|---|
| Market Scanner | Finds candidate companies and themes |
| Financial Data Agent | Collects market cap, EV, revenue, EBITDA, FCF, multiples |
| Filings Agent | Reads annual/quarterly reports, investor presentations |
| News & Geopolitics Agent | Analyzes macro, geopolitical and regulatory developments |
| Industry Research Agent | Builds industry context, peer group |
| Source Quality Agent | Scores evidence quality, flags weak sources |

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
