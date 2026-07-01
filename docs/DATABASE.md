# Database Schema

## Status: Phase 14 — Discovery Screener Tables Added (screening_universes, screening_runs, screening_candidates)

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
| 005 | `005_add_financial_snapshots.py` | creates company_financial_snapshots (JSONB snapshot storage with SHA-256 dedup hash, FK to companies and agent_runs) |
| 006 | `006_add_discovery_screener.py` | creates screening_universes, screening_runs, screening_candidates (Phase 14 company discovery funnel) |

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

### Financial Data Snapshots (Phase 13)

**company_financial_snapshots**
```
id                  UUID PK (default gen_random_uuid())
company_id          UUID NULLABLE FK → companies.id (SET NULL on delete)
ticker              VARCHAR(20) NOT NULL
exchange            VARCHAR(20) NULLABLE
agent_run_id        UUID NULLABLE FK → agent_runs.id (SET NULL on delete)
provider_name       VARCHAR(50) NOT NULL       "eodhd" | "mock" | etc.
source_tier         VARCHAR(50) NOT NULL       always "T5_api_aggregator" for EODHD
snapshot_type       VARCHAR(50) NOT NULL       "fundamentals" | "profile" | "price_history"
retrieved_at        TIMESTAMP WITH TIME ZONE NOT NULL
data_quality        VARCHAR(50) NOT NULL       DataQuality enum value
raw_payload_json    JSONB NULLABLE             full raw provider response
raw_payload_hash    VARCHAR(64) NULLABLE       SHA-256 hex digest for deduplication
datapoints_json     JSONB NULLABLE             extracted FundamentalDataPoint list
created_at          TIMESTAMP WITH TIME ZONE NOT NULL default now()

INDEX: ix_cfs_provider_ticker (provider_name, ticker) — compound
INDEX: ix_cfs_snapshot_type (snapshot_type)
INDEX: ix_cfs_company_id (company_id)
INDEX: ix_cfs_agent_run_id (agent_run_id)
INDEX: ix_cfs_retrieved_at (retrieved_at)
INDEX: ix_cfs_raw_payload_hash (raw_payload_hash) — for deduplication
```

`raw_payload_hash` enables deduplication: before persisting, callers can check whether an identical payload was already stored (same SHA-256). `company_id` and `agent_run_id` are SET NULL on referenced row deletion to preserve the snapshot history.

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

---

### Company Discovery / Screener (Phase 14)

**screening_universes**
```
id              UUID PK
name            VARCHAR(200) NOT NULL
description     TEXT NULLABLE
region          VARCHAR(100) NULLABLE
exchange        VARCHAR(50) NULLABLE
sector_filter   VARCHAR(100) NULLABLE
theme           VARCHAR(100) NULLABLE    one of: energy_transition | electrification_grid | defense_security | industrial_resilience | real_assets | materials_mining
provider_name   VARCHAR(50) NOT NULL DEFAULT 'mock'
created_at      TIMESTAMP WITH TIME ZONE NOT NULL

INDEX: theme, region, provider_name
```

**screening_runs**
```
id                  UUID PK
universe_id         UUID FK → screening_universes.id (RESTRICT)
status              VARCHAR(50) NOT NULL DEFAULT 'pending'   pending | running | completed | failed | cancelled
provider_name       VARCHAR(50) NOT NULL
started_at          TIMESTAMP WITH TIME ZONE NULLABLE
completed_at        TIMESTAMP WITH TIME ZONE NULLABLE
parameters_json     JSONB NULLABLE                           run parameters (max_candidates, market_cap range, keyword)
summary_json        JSONB NULLABLE                           result summary (total_candidates, status_counts, etc.)
error_message       TEXT NULLABLE
created_at          TIMESTAMP WITH TIME ZONE NOT NULL

INDEX: universe_id, status, provider_name, created_at
```

**screening_candidates**
```
id                      UUID PK
screening_run_id        UUID FK → screening_runs.id (CASCADE)
company_id              UUID NULLABLE FK → companies.id (SET NULL)   set on promotion
ticker                  VARCHAR(20) NOT NULL
exchange                VARCHAR(20) NULLABLE
name                    VARCHAR(200) NULLABLE
country                 VARCHAR(100) NULLABLE
sector                  VARCHAR(100) NULLABLE
provider_symbol         VARCHAR(50) NULLABLE        EODHD-format symbol (TICKER.EXCHANGE)
market_cap              NUMERIC(20,2) NULLABLE
currency                VARCHAR(10) NULLABLE
candidate_status        VARCHAR(50) NOT NULL DEFAULT 'candidate_found'
discovery_reasons_json  JSONB NULLABLE               list of human-readable discovery reason strings
available_data_json     JSONB NULLABLE               list of available field names
missing_data_json       JSONB NULLABLE               list of missing field names
source_tier             VARCHAR(50) NULLABLE         T5_api_aggregator for EODHD; T6_model_estimate for mock
data_quality            VARCHAR(50) NULLABLE         DataQuality enum value
warnings_json           JSONB NULLABLE               list of warning strings (T5 validation warning always present for EODHD)
created_at              TIMESTAMP WITH TIME ZONE NOT NULL

INDEX: screening_run_id, candidate_status, ticker, company_id
```

**candidate_status allowed values (internal only — never public recommendations):**

| Status | Meaning |
|---|---|
| `candidate_found` | Raw find; minimal data; not yet assessed |
| `needs_data` | More data needed before analysis can start |
| `needs_primary_sources` | T5/T6 data only; T1/T2 validation required |
| `ready_for_deeper_analysis` | Sufficient data for company-analysis workflow |
| `rejected_by_screen` | Did not meet screen criteria on closer inspection |
| `error` | Error occurred during candidate processing |

**Forbidden values (never stored in candidate_status):**
`BUY`, `SELL`, `HOLD`, `WATCH`, `price_target`, `fair_value`, `upside_percent`

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
