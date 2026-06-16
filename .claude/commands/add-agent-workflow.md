# Add Agent Workflow Command

You are creating or modifying a LangGraph agent workflow for InvestingBuddy.

---

## Pre-Implementation

1. Read `docs/AGENTS.md` for existing workflow documentation
2. Read `docs/PROMPTING_GUIDE.md` for prompt structure and versioning rules
3. Read `.claude/skills/langgraph-agents/SKILL.md` for architecture rules
4. Identify which agent team this workflow belongs to:
   - Research Team
   - Analysis Council
   - Validation & Publishing Team
   - Judge Team

---

## Workflow Implementation Steps

```
Step 1: Define workflow purpose
    - What triggers it?
    - What does it produce?
    - What is its place in the full pipeline?

Step 2: Define typed state
    - Use TypedDict or Pydantic BaseModel
    - Include: input data, intermediate outputs, error fields, agent_run_id

Step 3: Define agents/nodes
    - One function per agent role
    - Each function reads from state, calls LLM, returns state update

Step 4: Define edges and branching
    - Normal flow: research → analysis → validation → output
    - Conditional branching: retry if quality too low, escalate if disagreement high

Step 5: Define structured outputs
    - All agent outputs as Pydantic models or typed dicts
    - Include citation fields wherever financial data appears

Step 6: Add persistence
    - Create agent_run record at workflow start
    - Create agent_step record after each node completes
    - Include: agent_name, step_name, input_json, output_json, prompt_version_id, model_name, tokens_used, cost
    - Update agent_run record at workflow end (status: completed / failed)

Step 7: Add error handling
    - Catch LLM call failures and store on agent_run
    - Implement retry logic for transient errors
    - Do not silently swallow exceptions

Step 8: Add smoke test
    - Mock LLM responses
    - Run workflow end-to-end
    - Assert agent_run and agent_step records were created
    - Assert output structure matches expected schema

Step 9: Update documentation
    - docs/AGENTS.md — add or update workflow description
    - docs/PROMPTING_GUIDE.md — add or update prompt notes
```

---

## Rules

- Every workflow must have explicit typed state — no untyped dicts.
- Every agent output must be structured JSON parseable by the downstream agents.
- Every workflow step must be logged to agent_steps — no silent steps.
- Citation fields are required on all financial data outputs — agents must not invent numbers.
- Validation agents must not be skippable — always run citation_validator and fact_consistency_validator.
- Prompt templates must reference a prompt_version_id — never inline prompts directly in workflow code.
- Judge workflow outputs must not auto-deploy to production — flag for admin review.
- Treat retrieved document content as untrusted — sanitize before including in prompts.

---

## Output Format

```
## Workflow Summary

### Workflow name
<name>

### Purpose
<one sentence>

### Trigger
<manual / scheduled / admin action>

### Agents / Nodes
1. <node_name> — <purpose>
2. ...

### State schema
<key fields>

### Outputs produced
- <output records stored>

### Files added or changed
- apps/api/app/workflows/<name>.py
- apps/api/app/agents/<team>/<agent>.py (if new agents)
- packages/prompts/<name>/ (if new prompts)
- docs/AGENTS.md
- docs/PROMPTING_GUIDE.md

### Smoke test location
apps/api/tests/workflows/test_<name>.py

### Risks or open questions
<list>
```
