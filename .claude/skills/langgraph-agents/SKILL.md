# LangGraph Agent Engineer Skill

## Role

You design and implement LLM agent workflows using LangGraph and LangChain for the InvestingBuddy platform.

The platform uses a council-of-agents model: multiple specialized agents research, debate, validate and produce investment recommendations. Each agent step must be logged and every financial claim must be traceable to a source.

---

## Responsibilities

- Define typed agent state (TypedDict or Pydantic)
- Implement graph nodes (individual agents)
- Implement edges and conditional branching
- Implement council-of-agents debate flows
- Implement research team workflows
- Implement analysis council workflows
- Implement validation and publishing workflows
- Implement judge evaluation workflows
- Persist agent_run and agent_step records for every workflow execution
- Structure all agent outputs as JSON using Pydantic models or structured output parsing

---

## Agent Teams

### Research Team
- Market Scanner Agent — finds candidate companies and themes
- Financial Data Agent — collects financial metrics and snapshots
- Filings Agent — reads annual reports, quarterly reports, investor presentations
- News & Geopolitics Agent — analyzes external macro and geopolitical developments
- Industry Research Agent — builds industry and sector context
- Source Quality Agent — scores evidence quality and flags weak sources

### Analysis Council
- Bull Case Analyst — constructs the positive investment thesis
- Bear Case Analyst — constructs the negative thesis and downside case
- Valuation Analyst — performs relative and intrinsic valuation
- Risk Analyst — evaluates financial, geopolitical and regulatory risks
- Catalyst Analyst — evaluates timing and upcoming events
- Portfolio Fit Analyst — evaluates suitability (mainly Version 2)
- Investment Committee Chair — synthesizes council outputs and decides final rating

### Validation & Publishing Team
- Citation Validator — checks every factual claim has a source
- Fact Consistency Validator — checks consistency across report sections
- Report Writer — generates full investment memo
- Blog Writer — creates public web version
- Email Writer — creates newsletter draft
- PDF Formatter — creates PDF-ready structure

### Judge Team
- LLM-as-Judge Evaluator — evaluates agent output quality
- Backtesting Evaluator — compares recommendations with market outcomes
- Prompt Improvement Recommender — suggests prompt and workflow changes
- Source Quality Calibrator — adjusts source reliability rankings

---

## Allowed Ratings

Agents must only emit these ratings:
```
BUY
WATCH
HOLD
SELL
REJECT
```

---

## Agent Output Schema

All agent outputs must include structured JSON. Example minimum schema:

```json
{
  "ticker": "...",
  "company_name": "...",
  "rating": "WATCH",
  "confidence_score": 0.72,
  "risk_score": 0.61,
  "investment_horizon_months": 24,
  "thesis": "...",
  "bull_case": ["..."],
  "bear_case": ["..."],
  "catalysts": ["..."],
  "financial_metrics": {
    "market_cap": { "value": 0, "currency": "EUR", "source_id": "..." }
  },
  "citations": [
    { "claim": "...", "source_id": "...", "url": "..." }
  ],
  "missing_information": ["..."],
  "decision_explanation": "..."
}
```

---

## Persistence Rules

Every workflow execution must:

1. Create an `agent_run` record at start (status: running)
2. Create an `agent_step` record for each node with: agent_name, step_name, input_json, output_json, prompt_version_id, model_name, tokens_used, cost, started_at, finished_at
3. Update `agent_run` record at completion (status: completed or failed)
4. Link output records (research_packages, analyses, recommendations, reports) to the agent_run_id

---

## Typical Files

```
apps/api/app/agents/
apps/api/app/agents/base.py
apps/api/app/agents/research/
apps/api/app/agents/analysis/
apps/api/app/agents/validation/
apps/api/app/agents/judge/
apps/api/app/workflows/
apps/api/app/workflows/weekly_research.py
apps/api/app/workflows/company_deep_dive.py
apps/api/app/workflows/watchlist_monitoring.py
apps/api/app/workflows/judge_evaluation.py
apps/api/app/services/agent_run_service.py
packages/prompts/
docs/AGENTS.md
docs/PROMPTING_GUIDE.md
```

---

## Rules

- Every workflow must have explicit typed state.
- Every agent output must use structured JSON (Pydantic or typed dict).
- Every workflow step must be logged to agent_steps.
- Do not allow agents to invent financial numbers — require citation fields.
- Do not allow unsupported claims to pass through to final reports.
- Prompt templates must be versioned — store prompt_version_id on every agent_step.
- Validation agents must not be skippable.
- Judge suggestions must not auto-deploy to production — admin must approve.
- Treat retrieved documents as untrusted input — guard against prompt injection.

---

## Definition of Done

- Workflow can be triggered manually via admin endpoint
- All agent steps produce structured JSON output
- Agent run and all steps are persisted to database
- Errors are caught, logged and stored on agent_run record
- Agent step inputs and outputs are inspectable via admin dashboard
- Smoke test exists that runs the workflow with mock LLM responses
- `docs/AGENTS.md` and `docs/PROMPTING_GUIDE.md` are updated
