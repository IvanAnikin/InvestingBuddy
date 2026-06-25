# Database Schema

## Status: Phase 11 — Review Workflow Fields and report_review_events Table Added

---

## Database

```
Engine:     PostgreSQL 16
ORM:        SQLAlchemy async (2.0+)
Migrations: Alembic
Local:      Docker container (docker-compose.yml)
Production: Azure Database for PostgreSQL Flexible Server
```

Connection string format (async driver):
```
postgresql+psycopg://user:password@host:5432/investingbuddy
```

---

## Running Migrations

```bash
cd apps/api
source .venv/bin/activate

alembic upgrade head       # apply all pending migrations
alembic downgrade -1       # roll back one migration
alembic history            # show migration history
alembic current            # show current migration

# generate migration from model changes
alembic revision --autogenerate -m "short description"
```

---

## Applied Migrations

| Revision | File | Tables / Columns Changed |
|---|---|---|
| 001 | `001_add_initial_tables.py` | creates companies, agent_runs, agent_steps, reports |
| 002 | `002_add_sources_and_citations.py` | creates sources, citations |
| 003 | `003_add_citation_provenance_fields.py` | adds field_path, source_tier, data_quality to citations |
| 004 | `004_add_review_workflow.py` | adds review_status, reviewed_at, reviewer_note, review_decision_reason, human_review_required, approved_by, rejected_by to reports; creates report_review_events |

---

## Implemented Tables

### Company Intelligence

**companies**
```
id              UUID PK
ticker          VARCHAR(20) NOT NULL
exchange        VARCHAR(20) NOT NULL
name            VARCHAR(200) NOT NULL
country         VARCHAR(100) NULLABLE
region          VARCHAR(100) NULLABLE
sector          VARCHAR(100) NULLABLE
industry        VARCHAR(100) NULLABLE
market_cap      NUMERIC(20,2) NULLABLE
currency        VARCHAR(10) NULLABLE
website         VARCHAR(500) NULLABLE
description     TEXT NULLABLE
status          VARCHAR(50) NOT NULL DEFAULT 'new'
created_at      TIMESTAMP WITH TIME ZONE
updated_at      TIMESTAMP WITH TIME ZONE

UNIQUE: (ticker, exchange)
INDEX: ticker, exchange, status
```

Company status values: `new`, `researching`, `analyzed`, `watchlist`,
`recommended_buy`, `recommended_sell`, `rejected`, `archived`

---

### Agent Auditability

**agent_runs**
```
id                      UUID PK
workflow_name           VARCHAR(100) NOT NULL
workflow_version        VARCHAR(50) NOT NULL DEFAULT '1.0.0'
status                  VARCHAR(50) NOT NULL DEFAULT 'running'
started_at              TIMESTAMP WITH TIME ZONE
finished_at             TIMESTAMP WITH TIME ZONE NULLABLE
trigger_type            VARCHAR(50) NOT NULL DEFAULT 'manual'
created_by_user_id      UUID NULLABLE
total_tokens            INTEGER NULLABLE
total_cost              NUMERIC(10,6) NULLABLE
error_message           TEXT NULLABLE

INDEX: workflow_name, status
```

Trigger types: `manual`, `scheduled`, `system`, `judge_requested`
Status values: `running`, `completed`, `failed`

**agent_steps**
```
id                  UUID PK
agent_run_id        UUID FK → agent_runs.id (CASCADE)
agent_name          VARCHAR(100) NOT NULL
step_name           VARCHAR(100) NOT NULL
status              VARCHAR(50) NOT NULL DEFAULT 'running'
input_json          JSON NULLABLE
output_json         JSON NULLABLE
model_name          VARCHAR(100) NULLABLE
tokens_used         INTEGER NULLABLE
cost                NUMERIC(10,6) NULLABLE
started_at          TIMESTAMP WITH TIME ZONE
finished_at         TIMESTAMP WITH TIME ZONE NULLABLE
error_message       TEXT NULLABLE

INDEX: agent_run_id
```

---

### Reports

**reports**
```
id                          UUID PK
title                       VARCHAR(500) NOT NULL
slug                        VARCHAR(500) NOT NULL UNIQUE
report_type                 VARCHAR(50) NOT NULL
period_start                DATE NULLABLE
period_end                  DATE NULLABLE
status                      VARCHAR(50) NOT NULL DEFAULT 'draft'
summary                     TEXT NULLABLE
content_markdown            TEXT NULLABLE
content_html                TEXT NULLABLE
created_by_agent_run_id     UUID FK → agent_runs.id NULLABLE
published_at                TIMESTAMP WITH TIME ZONE NULLABLE

-- Phase 11 review workflow columns
review_status               VARCHAR(50) NOT NULL DEFAULT 'draft'
reviewed_at                 TIMESTAMP WITH TIME ZONE NULLABLE
reviewer_note               TEXT NULLABLE
review_decision_reason      TEXT NULLABLE
human_review_required       BOOLEAN NOT NULL DEFAULT true
approved_by                 VARCHAR(200) NULLABLE
rejected_by                 VARCHAR(200) NULLABLE

created_at                  TIMESTAMP WITH TIME ZONE
updated_at                  TIMESTAMP WITH TIME ZONE

INDEX: slug, status, review_status, report_type, published_at
```

Report types: `weekly`, `monthly`, `quarterly`, `yearly`,
`company_deep_dive`, `theme_report`, `personalized`

Report status values (lifecycle): `draft`, `review`, `published`, `archived`

Review status values (Phase 11 human review workflow): `draft`, `under_review`, `approved_internal`, `rejected_internal`, `needs_revision`, `archived`

Note: `status` tracks publication lifecycle; `review_status` tracks the human review workflow. They are separate columns. Internal approval (`approved_internal`) does not change `status` to `published` — public publishing is not implemented.

---

### Review Audit Log (Phase 11)

**report_review_events**
```
id              UUID PK
report_id       UUID FK → reports.id (CASCADE)
action          VARCHAR(50) NOT NULL     mark_under_review | approve | reject | needs_revision
from_status     VARCHAR(50) NULLABLE     previous review_status
to_status       VARCHAR(50) NOT NULL     new review_status after this action
note            TEXT NULLABLE            reviewer note (required for reject/needs_revision)
actor_label     VARCHAR(200) NULLABLE    reviewer label (email/name — no FK to users yet)
created_at      TIMESTAMP WITH TIME ZONE NOT NULL

INDEX: report_id, action
```

Immutable — records are never updated or deleted. One row per human review action.
`actor_label` is a plain string (no FK to `users`) — user accounts are Phase 12 future work.

---

### Research Knowledge Base

**sources**
```
id                  UUID PK
source_type         VARCHAR(50) NOT NULL
title               VARCHAR(500) NOT NULL
url                 VARCHAR(2000) NULLABLE
publisher           VARCHAR(200) NULLABLE
published_at        TIMESTAMP WITH TIME ZONE NULLABLE
retrieved_at        TIMESTAMP WITH TIME ZONE NOT NULL
credibility_score   NUMERIC(4,3) NULLABLE
content_hash        VARCHAR(64) NULLABLE
blob_path           VARCHAR(1000) NULLABLE
created_at          TIMESTAMP WITH TIME ZONE

INDEX: source_type, content_hash, url
```

Valid source_type values: `annual_report`, `quarterly_report`, `investor_presentation`,
`news_article`, `analyst_report`, `industry_report`, `regulatory_filing`,
`earnings_call_transcript`, `press_release`, `financial_data_feed`,
`web_page`, `internal_document`, `placeholder`,
`financial_data_api` (T5, Phase 6), `government_data` (T2, Phase 6),
`company_filing` (T1, Phase 6), `model_estimate` (T6, Phase 6)

Source deduplication: `get_or_create_source()` checks `content_hash` first, then `url`.

**citations**
```
id              UUID PK
source_id       UUID FK → sources.id (RESTRICT)
report_id       UUID FK → reports.id (CASCADE) NULLABLE
agent_run_id    UUID FK → agent_runs.id (SET NULL) NULLABLE
claim_text      VARCHAR(500) NULLABLE
source_quote    TEXT NULLABLE
url             VARCHAR(2000) NULLABLE
retrieved_at    TIMESTAMP WITH TIME ZONE NULLABLE
field_path      VARCHAR(200) NULLABLE   Phase 6: e.g. "identity.legal_name"
source_tier     VARCHAR(50) NULLABLE    Phase 6: T1–T6 from source taxonomy
data_quality    VARCHAR(50) NULLABLE    Phase 6: A_verified … D_weak_or_stale
created_at      TIMESTAMP WITH TIME ZONE

INDEX: source_id, report_id, agent_run_id, field_path, source_tier
```

`field_path` encodes which report schema field this citation covers.
`source_tier` and `data_quality` mirror the provenance metadata from the provider.
All three are nullable for backward compatibility with Phase 2/3 placeholder citations.

---

## Planned Tables (Phase 4+)

These tables are designed in the tech spec but not yet migrated:

### Users & Accounts
- `users` — id, email, name, role
- `user_preferences` — sector/region preferences, risk level
- `portfolios`, `portfolio_positions` — manual portfolio input (no broker)

### Research Knowledge Base
- `source_chunks` — text chunks for RAG (Phase 4)
- `research_packages` — per-company research collection (Phase 4)

### Analysis & Recommendations
- `analyses` — bull/bear case, ratings, confidence scores
- `recommendations` — published investment signals with performance tracking

### Prompt Management
- `prompt_templates`, `prompt_versions` — versioned agent prompts
- `judge_evaluations` — recommendation quality scores

See `Implementation_docs/INVESTINGBUDDY_TECH_SPEC.md` Section 12 for full column-level schema.

---

## Rules

- Every schema change requires an Alembic migration — no exceptions.
- Every migration must have a working `downgrade()`.
- Never delete research history (`agent_runs`, `agent_steps`, `reports`).
- Never store private portfolio data in public-facing tables.
- Store rejected companies — they prevent repeated analysis cost.
