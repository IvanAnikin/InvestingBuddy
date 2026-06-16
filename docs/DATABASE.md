# Database Schema

## Status: Placeholder — Phase 0

This document describes the InvestingBuddy PostgreSQL database schema.

Update this file when:
- A new table is added
- A column is added, renamed or removed
- An enum value is added
- An index is added or removed
- A foreign key relationship changes

For migration rules see `.claude/skills/database-design/SKILL.md`.

---

## Database

```
Engine:     PostgreSQL 16
ORM:        SQLAlchemy async
Migrations: Alembic
Local:      Docker container (docker-compose.yml — Phase 1)
Production: Azure Database for PostgreSQL Flexible Server
```

Connection string format:
```
postgresql+asyncpg://user:password@host:5432/investingbuddy
```

---

## Schema Status: Not Yet Created (Phase 1)

No tables exist yet. First migrations will be created in Phase 1.

---

## Planned Tables

### Users & Accounts

**users**
```
id              UUID PK
email           VARCHAR UNIQUE NOT NULL
name            VARCHAR
role            ENUM (public_user, subscriber, admin, super_admin)
created_at      TIMESTAMP
updated_at      TIMESTAMP
```

**user_preferences**
```
id                      UUID PK
user_id                 UUID FK → users.id
preferred_regions       JSONB
preferred_sectors       JSONB
excluded_sectors        JSONB
risk_level              VARCHAR
investment_horizon      VARCHAR
notification_frequency  VARCHAR
created_at              TIMESTAMP
updated_at              TIMESTAMP
```

**portfolios**
```
id              UUID PK
user_id         UUID FK → users.id
name            VARCHAR
base_currency   VARCHAR
created_at      TIMESTAMP
updated_at      TIMESTAMP
```

**portfolio_positions** (manual input only — no broker)
```
id                  UUID PK
portfolio_id        UUID FK → portfolios.id
ticker              VARCHAR
exchange            VARCHAR
company_id          UUID FK → companies.id NULLABLE
quantity            DECIMAL NULLABLE
average_price       DECIMAL NULLABLE
currency            VARCHAR
created_at          TIMESTAMP
updated_at          TIMESTAMP
```

---

### Company Intelligence

**companies**
```
id              UUID PK
ticker          VARCHAR NOT NULL
exchange        VARCHAR NOT NULL
name            VARCHAR NOT NULL
country         VARCHAR
region          VARCHAR
sector          VARCHAR
industry        VARCHAR
market_cap      DECIMAL NULLABLE
currency        VARCHAR
website         VARCHAR NULLABLE
description     TEXT NULLABLE
status          ENUM (new, researching, analyzed, watchlist, recommended_buy, recommended_sell, rejected, archived)
created_at      TIMESTAMP
updated_at      TIMESTAMP
INDEX: ticker, exchange, status
```

**company_financial_snapshots**
```
id                  UUID PK
company_id          UUID FK → companies.id
snapshot_date       DATE
market_cap          DECIMAL NULLABLE
enterprise_value    DECIMAL NULLABLE
revenue             DECIMAL NULLABLE
ebitda              DECIMAL NULLABLE
free_cash_flow      DECIMAL NULLABLE
cash                DECIMAL NULLABLE
debt                DECIMAL NULLABLE
net_debt            DECIMAL NULLABLE
ev_ebitda           DECIMAL NULLABLE
pe_ratio            DECIMAL NULLABLE
fcf_yield           DECIMAL NULLABLE
source_id           UUID FK → sources.id
created_at          TIMESTAMP
```

---

### Research Knowledge Base

**sources**
```
id                  UUID PK
source_type         VARCHAR (annual_report, quarterly_report, transcript, news, industry_report, etc.)
title               VARCHAR
url                 VARCHAR
publisher           VARCHAR NULLABLE
published_at        TIMESTAMP NULLABLE
retrieved_at        TIMESTAMP NOT NULL
credibility_score   DECIMAL NULLABLE
blob_path           VARCHAR NULLABLE
content_hash        VARCHAR NULLABLE (deduplication)
created_at          TIMESTAMP
INDEX: source_type, retrieved_at
```

**source_chunks**
```
id              UUID PK
source_id       UUID FK → sources.id
chunk_text      TEXT
chunk_index     INTEGER
embedding_id    VARCHAR NULLABLE (Azure AI Search document ID)
created_at      TIMESTAMP
```

**research_packages**
```
id              UUID PK
company_id      UUID FK → companies.id NULLABLE
theme_id        UUID FK → themes.id NULLABLE
agent_run_id    UUID FK → agent_runs.id
summary         TEXT NULLABLE
status          VARCHAR
created_at      TIMESTAMP
updated_at      TIMESTAMP
```

---

### Analysis & Recommendations

**analyses**
```
id                      UUID PK
company_id              UUID FK → companies.id
research_package_id     UUID FK → research_packages.id NULLABLE
agent_run_id            UUID FK → agent_runs.id
bull_case               TEXT NULLABLE
bear_case               TEXT NULLABLE
valuation_summary       TEXT NULLABLE
risk_summary            TEXT NULLABLE
catalyst_summary        TEXT NULLABLE
final_rating            ENUM (BUY, WATCH, HOLD, SELL, REJECT) NULLABLE
confidence_score        DECIMAL NULLABLE
risk_score              DECIMAL NULLABLE
created_at              TIMESTAMP
```

**recommendations**
```
id                      UUID PK
company_id              UUID FK → companies.id
analysis_id             UUID FK → analyses.id
rating                  ENUM (BUY, WATCH, HOLD, SELL, REJECT)
recommendation_date     DATE
publication_date        DATE NULLABLE
entry_price             DECIMAL NULLABLE
currency                VARCHAR
horizon_months          INTEGER
confidence_score        DECIMAL
risk_score              DECIMAL
benchmark_id            UUID NULLABLE
status                  ENUM (draft, review, published, closed, invalidated)
created_at              TIMESTAMP
updated_at              TIMESTAMP
INDEX: company_id, status, recommendation_date
```

**citations**
```
id                  UUID PK
report_id           UUID FK → reports.id NULLABLE
analysis_id         UUID FK → analyses.id NULLABLE
source_id           UUID FK → sources.id NOT NULL
claim_text          TEXT
source_quote        TEXT NULLABLE
url                 VARCHAR
retrieved_at        TIMESTAMP
created_at          TIMESTAMP
```

---

### Reports

**reports**
```
id                          UUID PK
title                       VARCHAR NOT NULL
slug                        VARCHAR UNIQUE NOT NULL
report_type                 ENUM (weekly, monthly, quarterly, yearly, company_deep_dive, theme_report, personalized)
period_start                DATE NULLABLE
period_end                  DATE NULLABLE
status                      ENUM (draft, review, published, archived)
summary                     TEXT NULLABLE
content_markdown            TEXT NULLABLE
content_html                TEXT NULLABLE
created_by_agent_run_id     UUID FK → agent_runs.id NULLABLE
published_at                TIMESTAMP NULLABLE
created_at                  TIMESTAMP
updated_at                  TIMESTAMP
INDEX: slug, status, published_at, report_type
```

**report_recommendations**
```
id                  UUID PK
report_id           UUID FK → reports.id
recommendation_id   UUID FK → recommendations.id
display_order       INTEGER
created_at          TIMESTAMP
```

---

### Agent Auditability

**agent_runs**
```
id                      UUID PK
workflow_name           VARCHAR
workflow_version        VARCHAR
status                  ENUM (running, completed, failed)
started_at              TIMESTAMP
finished_at             TIMESTAMP NULLABLE
trigger_type            ENUM (manual, scheduled, system, judge_requested)
created_by_user_id      UUID FK → users.id NULLABLE
total_tokens            INTEGER NULLABLE
total_cost              DECIMAL NULLABLE
error_message           TEXT NULLABLE
```

**agent_steps**
```
id                  UUID PK
agent_run_id        UUID FK → agent_runs.id
agent_name          VARCHAR
step_name           VARCHAR
status              ENUM (running, completed, failed)
input_json          JSONB NULLABLE
output_json         JSONB NULLABLE
prompt_version_id   UUID FK → prompt_versions.id NULLABLE
model_name          VARCHAR NULLABLE
tokens_used         INTEGER NULLABLE
cost                DECIMAL NULLABLE
started_at          TIMESTAMP
finished_at         TIMESTAMP NULLABLE
error_message       TEXT NULLABLE
```

---

### Prompt Management

**prompt_templates**
```
id              UUID PK
agent_name      VARCHAR
name            VARCHAR
description     TEXT NULLABLE
created_at      TIMESTAMP
```

**prompt_versions**
```
id                      UUID PK
prompt_template_id      UUID FK → prompt_templates.id
version                 VARCHAR
content                 TEXT
created_at              TIMESTAMP
created_by              VARCHAR NULLABLE
```

---

### Judge System

**judge_evaluations**
```
id                      UUID PK
recommendation_id       UUID FK → recommendations.id
agent_run_id            UUID FK → agent_runs.id
evaluation_date         DATE
performance_window      VARCHAR (1m, 3m, 6m, 12m, 24m, 36m)
actual_return           DECIMAL NULLABLE
benchmark_return        DECIMAL NULLABLE
sector_return           DECIMAL NULLABLE
alpha                   DECIMAL NULLABLE
max_drawdown            DECIMAL NULLABLE
quality_score           DECIMAL NULLABLE
reasoning_score         DECIMAL NULLABLE
citation_score          DECIMAL NULLABLE
risk_score              DECIMAL NULLABLE
judge_summary           TEXT NULLABLE
improvement_suggestions JSONB NULLABLE
created_at              TIMESTAMP
```

---

## Running Migrations

```bash
cd apps/api
alembic upgrade head       # apply all pending migrations
alembic downgrade -1       # roll back one migration
alembic history            # show migration history
alembic current            # show current migration
```
