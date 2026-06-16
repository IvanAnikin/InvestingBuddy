# Database Design Agent Skill

## Role

You design and maintain the PostgreSQL schema for InvestingBuddy using SQLAlchemy and Alembic.

---

## Responsibilities

- SQLAlchemy async ORM models
- Alembic migration files (upgrade and downgrade)
- Index design for frequently queried fields
- Foreign key relationships and constraints
- Enum definitions for status and type fields
- Data integrity rules
- Query performance considerations
- Auditability fields (timestamps, created_by, source_id)

---

## Core Tables

The platform stores these primary entities:

**Users & Accounts**
- `users` — id, email, name, role, created_at, updated_at
- `user_preferences` — id, user_id, preferred_regions, preferred_sectors, risk_level, investment_horizon, etc.
- `portfolios` — id, user_id, name, base_currency
- `portfolio_positions` — id, portfolio_id, ticker, company_id, quantity, average_price, currency

**Company Intelligence**
- `companies` — id, ticker, exchange, name, country, region, sector, industry, market_cap, currency, status
- `company_financial_snapshots` — id, company_id, snapshot_date, market_cap, enterprise_value, revenue, ebitda, fcf, ev_ebitda, pe_ratio, source_id

**Research Knowledge Base**
- `sources` — id, source_type, title, url, publisher, published_at, retrieved_at, credibility_score, blob_path
- `source_chunks` — id, source_id, chunk_text, chunk_index, embedding_id
- `research_packages` — id, company_id, theme_id, agent_run_id, summary, status

**Analysis & Recommendations**
- `analyses` — id, company_id, research_package_id, agent_run_id, bull_case, bear_case, valuation_summary, risk_summary, final_rating, confidence_score
- `recommendations` — id, company_id, analysis_id, rating, recommendation_date, publication_date, entry_price, confidence_score, risk_score, status
- `citations` — id, report_id, analysis_id, source_id, claim_text, source_quote, url, retrieved_at

**Reports**
- `reports` — id, title, slug, report_type, period_start, period_end, status, summary, content_markdown, created_by_agent_run_id, published_at
- `report_recommendations` — id, report_id, recommendation_id, display_order

**Agent Auditability**
- `agent_runs` — id, workflow_name, workflow_version, status, started_at, finished_at, trigger_type, total_tokens, total_cost, error_message
- `agent_steps` — id, agent_run_id, agent_name, step_name, status, input_json, output_json, prompt_version_id, model_name, tokens_used, cost, started_at, finished_at

**Prompt Management**
- `prompt_templates` — id, agent_name, name, description, created_at
- `prompt_versions` — id, prompt_template_id, version, content, created_at, created_by

**Judge System**
- `judge_evaluations` — id, recommendation_id, agent_run_id, evaluation_date, performance_window, actual_return, benchmark_return, alpha, quality_score, judge_summary, improvement_suggestions

---

## Status Enums

**Company status:** new, researching, analyzed, watchlist, recommended_buy, recommended_sell, rejected, archived

**Recommendation rating:** BUY, WATCH, HOLD, SELL, REJECT

**Recommendation status:** draft, review, published, closed, invalidated

**Report type:** weekly, monthly, quarterly, yearly, company_deep_dive, theme_report, personalized

**Agent run trigger:** manual, scheduled, system, judge_requested

---

## Rules

- Every schema change requires an Alembic migration — no exceptions.
- Every migration must have a working `downgrade()` method (unless truly irreversible with explicit comment).
- Prefer additive migrations — add columns before removing old ones.
- Always store `created_at` and `updated_at` on user-facing tables.
- Always store `source_id` on financial metric records.
- Never delete research history unless explicitly required by admin action.
- Use explicit foreign keys with meaningful names.
- Add indexes on foreign keys and frequently filtered fields (ticker, status, published_at, company_id).
- Do not store private user data (portfolio_positions) in public-facing tables.
- Store rejected companies — they are valuable for avoiding repeat analysis.

---

## Typical Files

```
apps/api/app/models/            # SQLAlchemy ORM models
apps/api/app/db/base.py         # declarative base
apps/api/app/db/session.py      # async session factory
apps/api/alembic/
apps/api/alembic/versions/      # migration files
apps/api/alembic/env.py
docs/DATABASE.md
```

---

## Migration Checklist

1. Inspect current models in `apps/api/app/models/`
2. Inspect latest migration in `apps/api/alembic/versions/`
3. Implement model changes
4. Run `alembic revision --autogenerate -m "description"`
5. Review the generated migration for correctness (autogenerate is not always accurate)
6. Verify `downgrade()` is reasonable
7. Run `alembic upgrade head` locally to test
8. Update `docs/DATABASE.md` with new or changed tables
9. Add model-level tests if validation logic exists

---

## Definition of Done

- SQLAlchemy model added or modified
- Alembic migration generated, reviewed and tested locally
- `downgrade()` verified to be safe
- Relationships and indexes are correct
- `docs/DATABASE.md` updated
- No migration skips or manual schema changes applied directly to the database
