# Agent Architecture

## Status: Placeholder — Phase 0

This document describes the InvestingBuddy multi-agent system.

Update this file when:
- A new agent is added or removed
- An agent's responsibilities change
- A new workflow is implemented
- A workflow step order changes
- State schemas change

For implementation rules see `.claude/skills/langgraph-agents/SKILL.md`.

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

## Team 1: Research Team

**Purpose:** Collect evidence, normalize sources and produce research packages.

| Agent | Responsibility |
|---|---|
| Market Scanner Agent | Finds candidate companies and themes for investigation |
| Financial Data Agent | Collects market cap, EV, revenue, EBITDA, FCF, multiples |
| Filings Agent | Reads annual/quarterly reports, investor presentations |
| News & Geopolitics Agent | Analyzes macro, geopolitical and regulatory developments |
| Industry Research Agent | Builds industry and sector context, peer group |
| Source Quality Agent | Scores evidence quality, flags weak or missing sources |

**Outputs:** `research_packages`, `sources`, `source_chunks`, `company_financial_snapshots`

---

## Team 2: Analysis Council

**Purpose:** Interpret research and produce a rated investment recommendation via structured debate.

| Agent | Responsibility |
|---|---|
| Bull Case Analyst | Constructs positive thesis, catalysts, upside case |
| Bear Case Analyst | Constructs negative thesis, downside risks, thesis-break conditions |
| Valuation Analyst | Performs relative valuation, DCF, EV/EBITDA, FCF yield |
| Risk Analyst | Evaluates financial, geopolitical, regulatory, liquidity risks |
| Catalyst Analyst | Identifies near-term and medium-term catalysts |
| Portfolio Fit Analyst | Evaluates diversification and suitability (mainly V2) |
| Investment Committee Chair | Synthesizes all agent outputs, resolves disagreements, assigns final rating |

**Outputs:** `analyses` with final_rating (BUY / WATCH / HOLD / SELL / REJECT), confidence_score, risk_score

---

## Team 3: Validation & Publishing Team

**Purpose:** Ensure quality and produce final report content.

| Agent | Responsibility |
|---|---|
| Citation Validator | Verifies every factual claim has a source, date, currency |
| Fact Consistency Validator | Checks for internal contradictions across sections |
| Report Writer | Generates full investment memo (admin view) |
| Blog Writer | Generates public web post version |
| Email Writer | Generates newsletter draft |
| PDF Formatter | Structures content for PDF generation |

**Outputs:** `reports` in various formats

---

## Team 4: Judge Team

**Purpose:** Evaluate agent system quality and produce improvement suggestions after recommendations mature.

| Agent | Responsibility |
|---|---|
| LLM-as-Judge Evaluator | Evaluates reasoning quality, citation quality, risk coverage |
| Backtesting Evaluator | Compares recommendations against actual market outcomes |
| Prompt Improvement Recommender | Suggests prompt and workflow changes |
| Source Quality Calibrator | Adjusts source credibility rankings |

**Outputs:** `judge_evaluations` — for admin review only, not auto-deployed

---

## Persistence

Every workflow must persist:
- `agent_runs` — one record per workflow execution
- `agent_steps` — one record per node execution, including input/output JSON

This enables debugging, auditing and judge evaluation.

---

## Implemented Workflows

None yet — Phase 2+

| Workflow | Status | Description |
|---|---|---|
| company_deep_dive | Not implemented | Manual ticker input → draft report |
| weekly_research | Not implemented | Scheduled full pipeline |
| watchlist_monitoring | Not implemented | Monitor existing positions |
| judge_evaluation | Not implemented | Post-publication quality assessment |

---

## Output Schema (Standard Fields)

All agent outputs must include:
```json
{
  "ticker": "...",
  "company_name": "...",
  "rating": "WATCH",
  "confidence_score": 0.0,
  "risk_score": 0.0,
  "citations": [],
  "missing_information": [],
  "decision_explanation": "..."
}
```

See `.claude/skills/langgraph-agents/SKILL.md` for full schema reference.
