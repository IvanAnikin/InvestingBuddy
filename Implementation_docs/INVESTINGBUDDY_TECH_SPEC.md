# InvestingBuddy Technical Specification & Implementation Plan

## 1. Project Purpose

InvestingBuddy is an AI-driven investment research platform focused on medium-term investment opportunities, primarily over a 6-month to 3-year horizon, with optional longer-term monitoring up to approximately 6 years.

The platform will analyze financial, geopolitical, macroeconomic, industry-specific and company-specific developments affecting listed companies. Its first version will produce high-quality general investment research reports for public users. Later versions will add personalized analysis based on user preferences and manually entered portfolio information.

The platform is not intended for day trading, high-frequency trading, broker integration or automatic trade execution. Users will make their own purchases and sales outside the platform.

---

## 2. Product Versions

## Version 1: General Research Platform

Version 1 focuses on public investment research.

Main goals:

- Generate high-quality weekly investment research.
- Focus first on European public companies.
- Prioritize real-asset and non-ICT sectors.
- Identify interesting companies, watchlist candidates and potential buy/sell signals.
- Store all research and analysis historically.
- Publish public reports as web pages.
- Later export selected reports as PDF brochures and email newsletters.
- Keep human admin review before public publication.

Primary users:

- General investors.
- Founders/admins reviewing investment opportunities.
- Readers interested in medium-term investment ideas.

---

## Version 2: Personalized Investor Assistant

Version 2 adds user-specific analysis behind login.

Main goals:

- Allow users to create account preferences.
- Allow users to manually enter portfolio holdings.
- Generate personalized suggestions.
- Compare new recommendations against user portfolios.
- Provide alerts based on user interests and risk preferences.
- Offer both general reports and personalized insights.

Important constraint:

- No broker account connection in early versions.
- No automatic trading.
- Users purchase and sell shares independently.

---

## 3. Geographic Market Scope

## Phase 1

Focus on European public companies.

Recommended starting regions:

- EU
- UK
- Switzerland
- Nordics
- Central and Eastern Europe

## Phase 2

Extend to:

- United States
- Canada
- Japan
- South Korea
- India
- selected Southeast Asian markets
- global companies exposed to strategic themes

The system should be architected as global from the beginning, but data ingestion should start with Europe to reduce complexity.

---

## 4. Investment Universes

## Default Universe: Real Assets / Material Economy

This should be the default analysis account type.

Focus areas:

- energy transition
- electrical grid
- electrification
- power generation
- batteries
- grid automation
- industrial automation
- commodities
- uranium
- copper
- rare earths
- recycling
- defense
- security
- surveillance
- logistics
- infrastructure
- reshoring
- nearshoring
- manufacturing
- autonomous mining
- maritime technology
- emerging-market infrastructure exposure

Preferred company profile:

- small and mid caps
- ideally below approximately 2B USD/EUR market cap
- under-researched
- exposed to macro/geopolitical tailwinds
- real products, infrastructure, contracts or tangible assets
- valuation not already excessively inflated

---

## Secondary Universe: IT / Technology Companies

Separate analysis profile focused on:

- software
- AI infrastructure
- data centers
- semiconductors
- cybersecurity
- enterprise IT
- cloud infrastructure
- developer tools
- automation software

This universe should be architecturally separate because valuation logic, risk profile, growth assumptions and data sources differ strongly from real-asset companies.

---

## 5. Recommended Technical Architecture

## High-Level Architecture

```text
Frontend Web App
        ↓
Backend API
        ↓
Agent Orchestration Layer
        ↓
Research / Analysis Services
        ↓
Data Storage + Vector Search
        ↓
Azure OpenAI / Azure AI Foundry
        ↓
Reports, Alerts, Dashboards
```

The platform should be treated as a research workflow system, not as a simple chatbot.

---

## 6. Recommended Stack

## Frontend

Recommended:

```text
Next.js / React / TypeScript
```

Responsibilities:

- public homepage
- public report archive
- weekly/monthly/quarterly/yearly reports
- company pages
- theme pages
- login
- user preferences
- portfolio input
- notification settings
- admin dashboard
- report review and publishing

Hosting:

```text
Azure App Service
```

Alternative later:

```text
Azure Static Web Apps
```

---

## Backend

Recommended:

```text
Python
FastAPI
SQLAlchemy
Pydantic
Alembic
```

Reasons:

- best ecosystem for LLM agents
- best compatibility with LangChain and LangGraph
- strong financial data tooling
- strong data analysis ecosystem
- easier integration with OpenBB, pandas, SEC parsers and document processing

Hosting:

```text
Azure App Service
```

Later scalable option:

```text
Azure Container Apps
```

---

## Agent Framework

Recommended:

```text
LangGraph
LangChain
Azure OpenAI
Azure AI Foundry
```

Use LangGraph for:

- multi-agent workflows
- stateful execution
- retry logic
- branching workflows
- human-in-the-loop review
- durable workflow state
- agent councils
- validation loops

Use LangChain for:

- tool wrappers
- document loaders
- retrievers
- model abstraction
- structured output parsing

---

## LLM Runtime Decision

Recommended:

```text
Azure OpenAI / Azure AI Foundry + LangGraph
```

Do not use as production runtime:

```text
GitHub Copilot CLI
Claude Code
Cursor Agent
Codex CLI
other coding assistants
```

Reason:

Coding agents are useful for development, but they are not appropriate as the runtime for a production investment research platform. They do not provide the required level of workflow control, auditability, permissions, scheduling, citations, structured storage, monitoring or deterministic orchestration.

Use coding agents only to help build the codebase.

---

## Database

Recommended:

```text
Azure Database for PostgreSQL
```

Use PostgreSQL for:

- users
- reports
- companies
- financial data
- agent runs
- watchlists
- portfolios
- recommendations
- citations
- backtesting results
- prompt versions
- evaluation scores

Optional extension:

```text
pgvector
```

However, for production-grade document search on Azure, prefer Azure AI Search.

---

## Vector Search / RAG

Recommended:

```text
Azure AI Search
```

Use for:

- company filings
- earnings call transcripts
- industry reports
- broker-like internal notes
- news summaries
- previous analyses
- research memo retrieval
- source-backed generation

---

## Storage

Recommended:

```text
Azure Blob Storage
```

Use for:

- PDFs
- annual reports
- filings
- downloaded documents
- exported PDF reports
- generated brochure files
- email templates
- raw source snapshots

---

## Background Jobs

Recommended:

```text
Azure Functions
```

Use for:

- weekly report generation
- monthly reports
- scheduled data refresh
- watchlist monitoring
- price updates
- backtesting updates
- notification sending

Alternative later:

```text
Azure Container Apps Jobs
```

Use Container Apps Jobs if workflows become long-running, container-heavy or compute-intensive.

---

## Queues

Recommended:

```text
Azure Service Bus
```

Use for:

- agent workflow tasks
- report generation tasks
- notification jobs
- document ingestion jobs
- valuation refresh jobs
- backtesting jobs

---

## Secrets

Recommended:

```text
Azure Key Vault
```

Store:

- Azure OpenAI keys
- data API keys
- database connection strings
- JWT secrets
- email provider credentials
- admin credentials

---

## Monitoring

Recommended:

```text
Application Insights
Azure Monitor
```

Track:

- failed agent runs
- token usage
- model cost
- data source errors
- latency
- report generation time
- user activity
- API errors
- scheduled job status

---

## Authentication

For MVP:

```text
Clerk
Auth0
or Microsoft Entra External ID
```

Recommended simple start:

```text
Clerk
```

Reason:

- fastest implementation
- clean Next.js integration
- good user management
- easy upgrade path

For deeper Microsoft ecosystem integration later:

```text
Microsoft Entra External ID
```

---

## Email / Notifications

Recommended:

```text
Azure Communication Services
```

Alternative:

```text
SendGrid
Postmark
Resend
```

Notification types:

- weekly report published
- company watchlist update
- personalized recommendation available
- price/valuation threshold hit
- thesis change warning
- quarterly portfolio review available

---

# 7. Core Application Modules

## 7.1 Public Report Module

Routes:

```text
/
/reports
/reports/weekly
/reports/monthly
/reports/quarterly
/reports/yearly
/reports/{slug}
/themes/{slug}
/companies/{ticker}
```

Functions:

- list public reports
- filter by date
- filter by theme
- filter by geography
- show report detail
- show citations
- show company cards
- show recommendation history

---

## 7.2 Admin Module

Routes:

```text
/admin
/admin/reports
/admin/reports/{id}
/admin/agent-runs
/admin/companies
/admin/watchlist
/admin/sources
/admin/prompts
/admin/evaluations
```

Functions:

- review generated reports
- approve publication
- reject weak reports
- rerun agent workflow
- inspect citations
- inspect agent debates
- inspect judge feedback
- manage prompt versions
- manage company universe
- manage themes
- manually add tickers

---

## 7.3 User Account Module

Version 2 feature.

Routes:

```text
/account
/account/preferences
/account/portfolio
/account/notifications
/account/recommendations
```

Functions:

- sector preferences
- region preferences
- risk tolerance
- investment horizon
- manual portfolio input
- notification settings
- personalized recommendations

---

## 7.4 Company Intelligence Module

Stores and displays:

- ticker
- exchange
- country
- sector
- industry
- market cap
- enterprise value
- revenue
- EBITDA
- free cash flow
- debt
- cash
- ownership
- management
- geographic exposure
- latest thesis
- latest rating
- recommendation history
- citations
- risks
- catalysts

---

## 7.5 Research Knowledge Base

Stores:

- documents
- source metadata
- extracted text
- chunk embeddings
- summaries
- source credibility scores
- linked companies
- linked themes
- linked reports

Source types:

- annual report
- quarterly report
- earnings transcript
- investor presentation
- regulatory filing
- industry report
- news article
- macro report
- government contract
- patent filing
- hiring trend data
- insider transaction data

---

# 8. Council-of-Agents Architecture

The system should use multiple groups of specialized agents. The goal is not one agent giving an answer, but a council of agents producing, debating, validating and publishing research.

---

## 8.1 Team 1: Research Team

Purpose:

Collect evidence, normalize sources and store research.

Agents:

### Market Scanner Agent

Finds companies and themes worth investigating.

Responsibilities:

- scan target markets
- detect macro/geopolitical tailwinds
- find under-researched companies
- identify unusual sector developments
- generate candidate ticker list

Output:

```text
candidate_companies
themes
reason_for_selection
source_links
initial_confidence
```

---

### Financial Data Agent

Collects financial metrics.

Responsibilities:

- market cap
- enterprise value
- revenue
- EBITDA
- free cash flow
- cash
- debt
- valuation multiples
- price history
- liquidity
- peer group data

Output:

```text
financial_snapshot
valuation_snapshot
source_citations
data_quality_notes
```

---

### Filings Agent

Reads official company documents.

Responsibilities:

- annual reports
- quarterly reports
- investor presentations
- management commentary
- risk disclosures
- segment reporting

Output:

```text
filing_summary
risk_factors
segment_notes
management_claims
citations
```

---

### News & Geopolitics Agent

Analyzes external developments.

Responsibilities:

- geopolitical events
- tariffs
- sanctions
- regulation
- defense spending
- energy policy
- commodity trends
- infrastructure policy

Output:

```text
external_tailwinds
external_headwinds
geopolitical_risk_notes
citations
```

---

### Industry Research Agent

Builds industry context.

Responsibilities:

- market size
- supply-demand dynamics
- competitors
- sector growth
- industry bottlenecks
- capital expenditure trends

Output:

```text
industry_overview
peer_group
sector_tailwinds
sector_risks
citations
```

---

### Source Quality Agent

Scores evidence quality.

Responsibilities:

- classify source reliability
- detect outdated data
- detect unsupported claims
- flag weak sources
- prefer primary sources

Output:

```text
source_quality_score
weak_sources
missing_sources
recommended_replacements
```

---

## 8.2 Team 2: Analysis Council

Purpose:

Interpret the research and debate investment decisions.

Agents:

### Bull Case Analyst

Creates the positive thesis.

Responsibilities:

- upside case
- catalysts
- structural tailwinds
- valuation upside
- competitive advantage
- why market may be missing the opportunity

Output:

```text
bull_case
upside_drivers
catalysts
confidence
```

---

### Bear Case Analyst

Creates the negative thesis.

Responsibilities:

- downside case
- valuation risk
- weak assumptions
- business risks
- debt risks
- liquidity risk
- why recommendation may fail

Output:

```text
bear_case
downside_drivers
thesis_break_conditions
confidence
```

---

### Valuation Analyst

Performs valuation.

Responsibilities:

- relative valuation
- peer valuation
- basic DCF
- FCF yield
- EV/EBITDA
- P/E
- scenario analysis

Output:

```text
valuation_summary
base_case_value
bull_case_value
bear_case_value
valuation_confidence
```

---

### Risk Analyst

Evaluates risk.

Responsibilities:

- financial risk
- geopolitical risk
- regulatory risk
- customer concentration
- commodity exposure
- currency exposure
- liquidity
- management risk

Output:

```text
risk_score
risk_breakdown
top_risks
risk_mitigants
```

---

### Catalyst Analyst

Evaluates timing.

Responsibilities:

- upcoming earnings
- contract awards
- policy changes
- industry events
- capex cycles
- commodity cycle
- management guidance

Output:

```text
expected_catalysts
time_horizon
near_term_triggers
medium_term_triggers
```

---

### Portfolio Fit Analyst

Mainly Version 2, but can exist in generic form in Version 1.

Responsibilities:

- sector concentration
- factor exposure
- volatility
- diversification
- correlation with existing holdings
- suitability for investor profile

Output:

```text
portfolio_fit_score
position_size_suggestion
diversification_notes
```

---

### Investment Committee Chair

Coordinates the council.

Responsibilities:

- compare agent outputs
- identify disagreements
- force resolution
- request additional research
- decide final rating

Possible ratings:

```text
BUY
WATCH
HOLD
SELL
REJECT
```

Output:

```text
final_rating
decision_summary
confidence_score
disagreement_log
required_follow_up
```

---

## 8.3 Team 3: Validation & Publishing Team

Purpose:

Validate quality and produce final outputs.

Agents:

### Citation Validator

Checks every factual claim.

Responsibilities:

- every number must have a source
- every financial metric must have source and date
- unsupported claims are removed or flagged
- contradictory numbers are highlighted

Output:

```text
citation_report
missing_citations
approved_claims
rejected_claims
```

---

### Fact Consistency Validator

Checks consistency across report sections.

Responsibilities:

- no contradictory recommendation
- consistent market cap
- consistent dates
- consistent valuation figures
- consistent risk rating

Output:

```text
consistency_status
issues
required_fixes
```

---

### Compliance / Safety Reviewer

Future legal module.

For now, keep as TODO.

Future responsibilities:

- disclaimers
- investment advice classification
- personalized advice limitations
- jurisdiction-specific rules
- marketing restrictions

Output:

```text
compliance_notes
required_disclaimers
risk_warnings
```

---

### Report Writer

Creates full investment memo.

Output:

```text
admin_full_report
```

---

### Blog Writer

Creates public web version.

Output:

```text
public_blog_post
```

---

### Email Writer

Creates newsletter version.

Output:

```text
email_subject
email_preview
email_body
```

---

### PDF / Brochure Formatter

Creates PDF-ready structure.

Output:

```text
brochure_title
executive_summary
company_cards
charts
risk_summary
citations
```

Actual PDF generation should be handled by backend rendering, not by the LLM.

---

# 9. LLM-as-Judge Evaluation Module

## Purpose

The LLM-as-Judge module evaluates the quality of the whole agent system and proposes improvements.

It should not directly fine-tune models or automatically change production prompts in the MVP.

Instead, it should:

- evaluate agent outputs
- compare recommendations with later market outcomes
- identify weak reasoning patterns
- identify missing data
- evaluate which agents were useful
- recommend prompt updates
- recommend scoring changes
- recommend tool usage changes
- recommend source priority changes

---

## Important Terminology

Avoid saying that the Judge "trains the models" in the first implementation.

More accurate:

```text
The Judge improves the agent system through prompt versioning, workflow changes, scoring calibration, source ranking and tool-use policy updates.
```

Possible later advanced features:

- supervised fine-tuning
- reward modeling
- model distillation
- custom classifier training
- ranking model training

But not for MVP.

---

## Judge Inputs

```text
research_package
analysis_council_output
final_recommendation
published_report
citations
agent_disagreement_log
historical_price_data
benchmark_data
sector_index_data
realized_events
future_financial_results
actual_return
benchmark_return
max_drawdown
volatility
```

---

## Judge Outputs

```text
analysis_quality_score
citation_quality_score
reasoning_quality_score
valuation_quality_score
risk_coverage_score
recommendation_quality_score
missed_risks
overconfidence_flags
underconfidence_flags
prompt_improvement_suggestions
workflow_improvement_suggestions
tool_usage_improvement_suggestions
source_quality_adjustments
scoring_matrix_adjustments
```

---

## Judge Evaluation Dimensions

### Factual Quality

Questions:

- Are numbers sourced?
- Are sources recent?
- Are sources reliable?
- Are there contradictory claims?
- Are assumptions clearly marked?

---

### Investment Reasoning Quality

Questions:

- Is the thesis clear?
- Is the valuation connected to the thesis?
- Are catalysts realistic?
- Is downside properly considered?
- Is the recommendation justified?

---

### Risk Quality

Questions:

- Are major risks identified?
- Is debt risk considered?
- Is liquidity risk considered?
- Is geopolitical exposure considered?
- Is regulatory risk considered?
- Is customer concentration considered?

---

### Performance Quality

Questions:

- Did BUY calls outperform benchmark?
- Did SELL calls avoid losses?
- Did WATCH candidates become opportunities?
- Did high-confidence calls perform better than low-confidence calls?
- Did the model miss obvious negative signals?

---

## Judge Workflow

```text
Completed recommendation
        ↓
Stored in recommendation database
        ↓
Price and benchmark tracked over time
        ↓
Performance calculated at 1m / 3m / 6m / 12m / 24m / 36m
        ↓
Judge evaluates original thesis vs actual outcome
        ↓
Judge proposes system improvements
        ↓
Admin reviews improvements
        ↓
Approved prompt/workflow version deployed
```

---

# 10. Backtesting & Performance Tracking

Every recommendation should be stored as an auditable investment signal.

## Recommendation Record

Store:

```text
ticker
exchange
company_id
recommendation_type
recommendation_date
publication_date
entry_price
currency
market_cap_at_recommendation
investment_horizon
confidence_score
risk_score
target_price_optional
base_case
bull_case
bear_case
expected_catalysts
thesis_break_conditions
benchmark_id
sector_index_id
agent_run_id
prompt_version_id
```

---

## Performance Windows

Calculate performance after:

```text
1 month
3 months
6 months
12 months
24 months
36 months
```

Metrics:

```text
absolute_return
benchmark_relative_return
sector_relative_return
max_drawdown
volatility
hit_rate
risk_adjusted_return
```

---

## Signal Quality Metrics

Track:

```text
BUY_hit_rate
BUY_average_alpha
SELL_loss_avoidance
WATCH_conversion_rate
confidence_score_correlation
risk_score_correlation
agent_accuracy_by_role
source_predictiveness
theme_success_rate
```

---

## Backtesting Warning

Avoid overfitting.

The Judge should not simply optimize for historical profit. It must also evaluate:

- risk-adjusted return
- drawdown
- false positives
- false negatives
- source quality
- reasoning quality
- benchmark-relative performance
- sector-relative performance

---

# 11. Prompt Versioning System

Every production prompt must be versioned.

Tables:

```text
prompt_templates
prompt_versions
agent_prompt_assignments
workflow_versions
judge_recommendations
approved_prompt_changes
```

Each agent run must store:

```text
agent_name
prompt_version
model_name
temperature
tools_enabled
context_sources
output
cost
latency
```

This allows future analysis of which prompt versions produced better recommendations.

---

# 12. Data Model Draft

## users

```text
id
email
name
role
created_at
updated_at
```

Roles:

```text
public_user
subscriber
admin
super_admin
```

---

## user_preferences

```text
id
user_id
preferred_regions
preferred_sectors
excluded_sectors
risk_level
investment_horizon
notification_frequency
created_at
updated_at
```

---

## portfolios

```text
id
user_id
name
base_currency
created_at
updated_at
```

---

## portfolio_positions

```text
id
portfolio_id
ticker
exchange
company_id
quantity_optional
average_price_optional
currency
created_at
updated_at
```

Manual input only. No broker connection.

---

## companies

```text
id
ticker
exchange
name
country
region
sector
industry
market_cap
currency
website
description
status
created_at
updated_at
```

Company status:

```text
new
researching
analyzed
watchlist
recommended_buy
recommended_sell
rejected
archived
```

---

## company_financial_snapshots

```text
id
company_id
snapshot_date
market_cap
enterprise_value
revenue
ebitda
free_cash_flow
cash
debt
net_debt
ev_ebitda
pe_ratio
fcf_yield
source_id
created_at
```

---

## sources

```text
id
source_type
title
url
publisher
published_at
retrieved_at
credibility_score
blob_path
content_hash
created_at
```

---

## source_chunks

```text
id
source_id
chunk_text
chunk_index
embedding_id
created_at
```

Embeddings may be stored in Azure AI Search instead of PostgreSQL.

---

## research_packages

```text
id
company_id
theme_id
agent_run_id
summary
status
created_at
updated_at
```

---

## analyses

```text
id
company_id
research_package_id
agent_run_id
bull_case
bear_case
valuation_summary
risk_summary
catalyst_summary
final_rating
confidence_score
risk_score
created_at
```

---

## recommendations

```text
id
company_id
analysis_id
rating
recommendation_date
publication_date
entry_price
currency
horizon_months
confidence_score
risk_score
benchmark_id
status
created_at
updated_at
```

Recommendation status:

```text
draft
review
published
closed
invalidated
```

---

## reports

```text
id
title
slug
report_type
period_start
period_end
status
summary
content_markdown
content_html
created_by_agent_run_id
published_at
created_at
updated_at
```

Report types:

```text
weekly
monthly
quarterly
yearly
company_deep_dive
theme_report
personalized
```

---

## report_recommendations

```text
id
report_id
recommendation_id
display_order
created_at
```

---

## citations

```text
id
report_id
analysis_id
source_id
claim_text
source_quote_optional
url
retrieved_at
created_at
```

---

## agent_runs

```text
id
workflow_name
workflow_version
status
started_at
finished_at
trigger_type
created_by_user_id
total_tokens
total_cost
error_message
```

Trigger types:

```text
manual
scheduled
system
judge_requested
```

---

## agent_steps

```text
id
agent_run_id
agent_name
step_name
status
input_json
output_json
prompt_version_id
model_name
tokens_used
cost
started_at
finished_at
error_message
```

---

## judge_evaluations

```text
id
recommendation_id
agent_run_id
evaluation_date
performance_window
actual_return
benchmark_return
sector_return
alpha
max_drawdown
quality_score
reasoning_score
citation_score
risk_score
judge_summary
improvement_suggestions
created_at
```

---

# 13. Main Workflows

## 13.1 Weekly General Research Workflow

```text
1. Scheduled trigger starts weekly workflow.
2. Market Scanner Agent identifies candidate themes and companies.
3. Research Team collects financial, filing, news and industry evidence.
4. Research packages are stored.
5. Analysis Council debates selected candidates.
6. Investment Committee creates draft recommendations.
7. Validation Team checks facts, citations and consistency.
8. Report Writer generates full draft report.
9. Blog Writer creates public version.
10. Email Writer creates newsletter draft.
11. Admin reviews report.
12. Admin approves publication.
13. Public report appears on website.
14. Notification is sent to subscribers.
```

---

## 13.2 Company Deep-Dive Workflow

```text
1. Admin enters ticker or selects watchlist company.
2. Research Team collects latest evidence.
3. Company Analyst creates business overview.
4. Valuation Analyst builds valuation view.
5. Risk Analyst creates risk review.
6. Bull and Bear agents debate thesis.
7. Investment Committee assigns rating.
8. Validation Team checks citations.
9. Draft company report is created.
10. Admin publishes or archives.
```

---

## 13.3 Watchlist Monitoring Workflow

```text
1. Scheduled job checks watchlist companies.
2. System updates price, valuation and news.
3. Catalyst Agent checks expected events.
4. Risk Agent checks thesis-breaking signals.
5. System flags changes.
6. Admin receives watchlist update.
```

---

## 13.4 Judge Evaluation Workflow

```text
1. Scheduled job checks old recommendations.
2. System calculates performance after defined windows.
3. Judge compares original thesis with actual outcome.
4. Judge identifies what worked and failed.
5. Judge proposes improvements.
6. Admin reviews improvements.
7. Approved improvements become new prompt/workflow versions.
```

---

## 13.5 Personalized Recommendation Workflow

Version 2.

```text
1. User sets preferences and manually enters holdings.
2. System maps portfolio exposure.
3. General recommendations are filtered for relevance.
4. Portfolio Fit Analyst evaluates suitability.
5. Personalized report is generated.
6. User receives private dashboard insight and optional notification.
```

---

# 14. API Structure

## Public API

```text
GET /api/reports
GET /api/reports/{slug}
GET /api/themes
GET /api/themes/{slug}
GET /api/companies/{ticker}
```

---

## Authenticated User API

```text
GET /api/me
PUT /api/me/preferences
GET /api/me/portfolio
POST /api/me/portfolio/positions
PUT /api/me/portfolio/positions/{id}
DELETE /api/me/portfolio/positions/{id}
GET /api/me/recommendations
GET /api/me/notifications
PUT /api/me/notifications
```

---

## Admin API

```text
GET /api/admin/reports
POST /api/admin/reports/{id}/publish
POST /api/admin/reports/{id}/reject
GET /api/admin/agent-runs
POST /api/admin/workflows/weekly-research/run
POST /api/admin/workflows/company-deep-dive/run
POST /api/admin/companies
GET /api/admin/watchlist
POST /api/admin/prompts/{id}/approve
GET /api/admin/judge-evaluations
```

---

# 15. Repository Structure

Recommended monorepo:

```text
investingbuddy/
│
├── apps/
│   ├── web/
│   │   ├── app/
│   │   ├── components/
│   │   ├── lib/
│   │   ├── public/
│   │   └── package.json
│   │
│   └── api/
│       ├── app/
│       │   ├── main.py
│       │   ├── core/
│       │   ├── db/
│       │   ├── models/
│       │   ├── schemas/
│       │   ├── api/
│       │   ├── services/
│       │   ├── agents/
│       │   ├── workflows/
│       │   ├── integrations/
│       │   └── jobs/
│       ├── alembic/
│       ├── tests/
│       └── pyproject.toml
│
├── packages/
│   ├── shared-types/
│   └── prompts/
│
├── infra/
│   ├── azure/
│   ├── docker/
│   └── terraform/
│
├── docs/
│   ├── TECH_SPEC.md
│   ├── AGENTS.md
│   ├── DATABASE.md
│   ├── API.md
│   └── ROADMAP.md
│
├── .env.example
├── docker-compose.yml
└── README.md
```

---

# 16. Backend Folder Detail

```text
apps/api/app/
│
├── main.py
│
├── core/
│   ├── config.py
│   ├── security.py
│   ├── logging.py
│   └── exceptions.py
│
├── db/
│   ├── session.py
│   ├── base.py
│   └── migrations.py
│
├── models/
│   ├── user.py
│   ├── company.py
│   ├── report.py
│   ├── source.py
│   ├── agent_run.py
│   ├── recommendation.py
│   └── judge.py
│
├── schemas/
│   ├── user.py
│   ├── company.py
│   ├── report.py
│   ├── source.py
│   ├── agent.py
│   └── recommendation.py
│
├── api/
│   ├── public/
│   ├── user/
│   └── admin/
│
├── agents/
│   ├── base.py
│   ├── research/
│   ├── analysis/
│   ├── validation/
│   └── judge/
│
├── workflows/
│   ├── weekly_research.py
│   ├── company_deep_dive.py
│   ├── watchlist_monitoring.py
│   ├── personalized_review.py
│   └── judge_evaluation.py
│
├── integrations/
│   ├── azure_openai.py
│   ├── azure_ai_search.py
│   ├── blob_storage.py
│   ├── openbb.py
│   ├── financial_data.py
│   ├── news.py
│   └── email.py
│
├── services/
│   ├── report_service.py
│   ├── company_service.py
│   ├── source_service.py
│   ├── recommendation_service.py
│   ├── valuation_service.py
│   └── backtesting_service.py
│
└── jobs/
    ├── weekly_report_job.py
    ├── price_update_job.py
    ├── watchlist_job.py
    └── judge_job.py
```

---

# 17. Development Phases

## Phase 0: Planning & Local Setup

Deliverables:

- repository initialized
- backend FastAPI running locally
- frontend Next.js running locally
- PostgreSQL local Docker container
- basic database models
- .env.example
- Azure resource plan
- first prompt templates

---

## Phase 1: Core App Skeleton

Deliverables:

- public report list page
- report detail page
- admin login
- admin report dashboard
- PostgreSQL schema
- CRUD for companies
- CRUD for reports
- CRUD for sources
- basic markdown report rendering

---

## Phase 2: First Agent Workflow

Deliverables:

- LangGraph installed
- Azure OpenAI connected
- one Market Scanner Agent
- one Company Analyst Agent
- one Investment Committee Agent
- manual workflow trigger from admin
- agent run logging
- generated draft report saved in DB

---

## Phase 3: Research Storage & Citations

Deliverables:

- source table
- citation table
- Blob Storage integration
- Azure AI Search integration
- source ingestion pipeline
- citation validator
- report claim/source linking

---

## Phase 4: Full Council-of-Agents MVP

Deliverables:

- Research Team
- Analysis Council
- Validation Team
- disagreement log
- final rating workflow
- admin review screen
- publish/reject actions

---

## Phase 5: Weekly Report Pipeline

Deliverables:

- scheduled weekly workflow
- public weekly report archive
- monthly/quarterly/yearly report types
- email draft generation
- PDF-ready report structure
- watchlist table

---

## Phase 6: Judge + Backtesting

Deliverables:

- recommendation performance tracking
- benchmark comparison
- judge evaluation workflow
- prompt versioning
- judge improvement suggestions
- admin review of system improvements

---

## Phase 7: Personalized Version

Deliverables:

- user preferences
- manual portfolio input
- private recommendations
- portfolio fit agent
- notification preferences
- private dashboard

---

# 18. MVP Scope Recommendation

Do not build everything at once.

## MVP Should Include

```text
public reports
admin dashboard
company database
manual ticker input
first agent workflow
research storage
citations
report publishing
basic watchlist
```

## MVP Should Not Include Yet

```text
broker integration
automatic trading
personalized regulated advice
fully automated publishing
complex PDF design
fine-tuned models
direct model training
social/community features
mobile app
```

---

# 19. First Real MVP Workflow

Build this first:

```text
Admin enters 5-20 European tickers
        ↓
Research Team gathers basic data
        ↓
Company Analyst creates short memo
        ↓
Bull/Bear/Valuation/Risk agents debate
        ↓
Investment Committee gives rating
        ↓
Citation Validator checks claims
        ↓
Admin reviews
        ↓
Published as weekly report
```

This avoids the hardest initial problem: fully automated company discovery.

After quality is good, add automatic market scanning.

---

# 20. Recommended First Implementation Commands

## Backend

```bash
mkdir investingbuddy
cd investingbuddy
mkdir -p apps/api
cd apps/api

python -m venv .venv
source .venv/bin/activate

pip install fastapi uvicorn sqlalchemy alembic psycopg[binary] pydantic pydantic-settings python-dotenv
pip install langchain langgraph langchain-openai langchain-community
pip install pandas numpy requests beautifulsoup4
pip install openbb
```

---

## Frontend

```bash
cd ../../
mkdir -p apps/web
cd apps/web

npx create-next-app@latest .
```

Recommended options:

```text
TypeScript: yes
ESLint: yes
Tailwind: yes
App Router: yes
src directory: yes
```

---

## Local Database

```yaml
# docker-compose.yml

services:
  postgres:
    image: postgres:16
    container_name: investingbuddy-postgres
    environment:
      POSTGRES_USER: investingbuddy
      POSTGRES_PASSWORD: investingbuddy
      POSTGRES_DB: investingbuddy
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

Run:

```bash
docker compose up -d
```

---

# 21. Environment Variables

```bash
DATABASE_URL=postgresql+psycopg://investingbuddy:investingbuddy@localhost:5432/investingbuddy

AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_API_VERSION=
AZURE_OPENAI_DEPLOYMENT_NAME=

AZURE_STORAGE_CONNECTION_STRING=
AZURE_STORAGE_CONTAINER_NAME=

AZURE_SEARCH_ENDPOINT=
AZURE_SEARCH_API_KEY=
AZURE_SEARCH_INDEX_NAME=

APP_ENV=development
SECRET_KEY=
```

---

# 22. First Agent Output Schema

All agent outputs should be structured JSON.

Example:

```json
{
  "ticker": "EXAMPLE",
  "company_name": "Example Company",
  "rating": "WATCH",
  "confidence_score": 0.72,
  "risk_score": 0.61,
  "investment_horizon_months": 24,
  "thesis": "Short thesis here.",
  "bull_case": ["Reason 1", "Reason 2"],
  "bear_case": ["Risk 1", "Risk 2"],
  "catalysts": ["Catalyst 1", "Catalyst 2"],
  "financial_metrics": {
    "market_cap": {
      "value": 1000000000,
      "currency": "EUR",
      "source_id": "source-id"
    }
  },
  "citations": [
    {
      "claim": "Example claim.",
      "source_id": "source-id",
      "url": "https://example.com"
    }
  ],
  "missing_information": ["Missing info 1"],
  "decision_explanation": "Why the rating was selected."
}
```

---

# 23. Report Types

## Weekly Report

Purpose:

- regular public investment intelligence
- top opportunities
- watchlist changes
- market developments

---

## Monthly Report

Purpose:

- deeper trend review
- sector-level summary
- performance review

---

## Quarterly Report

Purpose:

- strategic investment themes
- thesis updates
- portfolio-like review
- major macro/geopolitical changes

---

## Yearly Report

Purpose:

- annual market outlook
- best themes for next year
- performance analysis
- lessons from Judge module

---

## Company Deep Dive

Purpose:

- single company investment memo
- detailed analysis
- valuation
- risks
- catalysts

---

## Personalized Report

Version 2.

Purpose:

- user-specific recommendation set
- portfolio fit analysis
- risk concentration
- watchlist alerts

---

# 24. Key System Rules

## Rule 1: No Unsupported Numbers

Every financial number must have:

```text
source
date
currency
retrieval timestamp
```

---

## Rule 2: No Direct Trading

The platform provides research and suggestions only.

No automatic execution.

---

## Rule 3: Human Review Before Publication

In Version 1, all public reports should be reviewed by admin before publication.

---

## Rule 4: Store Every Agent Run

Every agent run must be logged for debugging, audit and improvement.

---

## Rule 5: Store Rejected Companies

Rejected companies are valuable. Store them with reasons to avoid repeated token cost.

---

## Rule 6: Judge Does Not Auto-Deploy Changes

Judge suggestions must be approved by admin before changing production prompts or workflows.

---

## Rule 7: Separate Public and Personalized Research

Public general research can be visible to everyone.

Personalized recommendations must be behind login.

---

# 25. Future TODO List

## Legal / Compliance

Later investigate:

- EU investment advice rules
- Czech financial regulation
- MiFID II implications
- personalized advice restrictions
- required disclaimers
- marketing language
- suitability assessment
- difference between research, education and advice
- liability limits
- terms of service
- privacy policy
- GDPR
- data retention
- user profiling

---

## Data Expansion

Later add:

- more financial APIs
- patent data
- hiring data
- insider transactions
- government contracts
- supply chain data
- satellite/commodity data
- alternative data

---

## Model Improvements

Later add:

- model comparison
- ensemble model outputs
- fine-tuning on internal style
- custom ranking models
- factor models
- quantitative screening
- automated chart generation
- explainable scoring dashboards

---

## Product Expansion

Later add:

- paid subscriptions
- private reports
- PDF exports
- model portfolios
- alerts
- personalized watchlists
- team accounts
- API access
- mobile app

---

# 26. Model Portfolio Decision

Model portfolios are not necessary for MVP.

Recommended approach:

## Version 1

Do not create model portfolios yet.

Use:

```text
BUY / WATCH / HOLD / SELL / REJECT
```

and provide generic position-size guidance such as:

```text
small speculative position
medium-conviction idea
watch only
avoid
```

## Version 2

Add optional model portfolios:

```text
Real Assets Europe
Global Infrastructure
Energy Transition
Defense & Security
Technology Growth
Balanced Medium-Term
```

These can be useful for users who want simplified portfolio construction, but they also increase regulatory and compliance complexity. Keep them as a later feature.

---

# 27. Immediate Next Development Task

The first coding milestone should be:

```text
Create the monorepo with FastAPI backend, Next.js frontend, PostgreSQL database, basic company/report tables, and one manual agent workflow that generates a draft company analysis from a manually provided ticker.
```

Do not start with fully automated market scanning. Start with manual ticker input and high-quality analysis.

---

# 28. Definition of Done for MVP

MVP is complete when:

- admin can add companies/tickers
- admin can run an analysis workflow
- agents generate a structured investment memo
- every factual claim can be linked to a source
- report draft is stored in database
- admin can edit/review/publish report
- public users can read published reports
- watchlist companies are stored
- recommendations are stored for future backtesting
- judge can evaluate at least historical recommendations manually or from stored data

---

# 29. Strategic Product Principle

The long-term value of InvestingBuddy is not only the LLM output.

The real moat is the accumulated research database:

- analyzed companies
- rejected companies
- watchlist history
- thesis versions
- recommendations
- citations
- performance outcomes
- judge evaluations
- prompt versions
- agent decision history

The LLM agents may change over time, but the research memory and evaluation history become the platform’s core asset.
