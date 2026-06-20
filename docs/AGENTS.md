# Agent Architecture

## Status: Phase 2 — Company analysis workflow skeleton implemented

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

### company_analysis — Phase 2 skeleton

**Trigger:** `POST /api/v1/workflows/company-analysis/run`

**Input:** company UUID (must exist in `companies` table) or ticker + exchange

**Purpose:** Runs a stub company analysis that creates a draft report.
Currently uses deterministic placeholder logic — no LLM calls.
Designed so that real LLM nodes can be dropped in per node without changing the graph structure.

**Graph:**

```
initialize
    ↓ (company found?)
    ├── No → handle_error → END
    └── Yes → analyze_company → save_report → finalize → END
```

**Nodes:**

| Node | Agent Name | Step Name | What it does |
|---|---|---|---|
| initialize | WorkflowController | initialize | Creates agent_run record, loads company from DB |
| analyze_company | CompanyAnalyst | analyze_company | Produces structured placeholder analysis JSON |
| save_report | ReportWriter | save_draft_report | Saves draft report to `reports` table |
| finalize | WorkflowController | finalize | Marks agent_run as completed |
| handle_error | WorkflowController | handle_error | Marks agent_run as failed |

**Source:** `apps/api/app/workflows/company_analysis.py`

**Output state fields:**
```python
{
  "agent_run_id": "uuid",
  "company_name": "Volkswagen AG",
  "ticker": "VOW3",
  "analysis_output": { ... },   # see output schema below
  "draft_report_id": "uuid",
  "status": "completed" | "failed",
  "error": None | "error message"
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

## Planned Workflows (Phase 3+)

| Workflow | Status | Description |
|---|---|---|
| company_analysis | ✅ Skeleton (Phase 2) | Manual ticker input → draft report (placeholder) |
| company_analysis (real LLM) | Phase 3 | Full analysis with Azure OpenAI + citations |
| weekly_research | Phase 5 | Scheduled full research pipeline |
| watchlist_monitoring | Phase 5 | Monitor existing watchlist positions |
| judge_evaluation | Phase 6 | Post-publication quality assessment |

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
