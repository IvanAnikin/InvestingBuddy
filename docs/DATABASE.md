# Database Schema

## Status: Phase 16 — Final Report Generator columns added (reports table)

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
| 007 | `007_add_scorecards.py` | creates scorecards (Phase 15 multi-dimension research attractiveness scoring) |
| 008 | `008_add_final_report_fields.py` | adds `final_report_version`, `safety_validation_json`, `schema_validation_json`, `source_summary_json`, `scorecard_id` to reports (Phase 16 Final Report Generator) |
| 009 | `009_add_backtesting_tables.py` | creates `backtest_runs`, `backtest_results`, `thesis_tracking_events` (Phase 22 Judge + Backtesting Framework) |
| 010 | `010_add_market_discovery.py` | creates `discovery_runs`, `discovery_candidates` (Phase 25 Real Market Candidate Discovery) |
| 011 | `011_add_thesis_discovery.py` | adds thesis columns to `discovery_runs` (`mode`, `thesis_text`, `parsed_thesis_json`, `universe_json`) + `discovery_candidates` (`thesis_relevance_score`, `combined_internal_score`, `thesis_match_json`) (Phase 27 Thesis-to-Universe Discovery) |
| 012 | `012_add_report_company_id.py` | adds `company_id` (UUID FK → companies.id, SET NULL) + index `ix_reports_company_id` to `reports` (Phase 32A hotfix — company-scoped `from-company` final-report selection) |
| 013 | `013_add_extracted_documents.py` | creates `extracted_documents`, `extracted_facts` (Phase 32A Slice 5A primary-document ingestion). Reversible, additive, backfill-free. **APPLIED on staging 2026-08-04** — Slice 5A CLOSED + STAGING-VALIDATED (`354a5ba`). |
| 014 | `014_add_document_ingestion_attempts.py` | creates `document_ingestion_attempts` — one honest row per primary-document ingestion attempt, **including the failed ones** (Phase 32A Slice 5B.1). Reversible, additive, backfill-free. **APPLIED + verified on staging 2026-08-05** — Slice 5B.1 CLOSED + STAGING-VALIDATED. |
| 015 | `015_add_field_review.py` | creates `field_review_runs`, `field_review_candidate_summaries` — the Deep Field Review, a COMPARATIVE council over the already-completed analyses of 2+ candidates from one discovery run (Phase 32A Slice 6D). Reversible, additive, backfill-free. **APPLIED + schema-verified on staging 2026-08-10** (`alembic current` = `015`, head) — Slice 6D CLOSED + STAGING-VALIDATED (PR #91 `dee5998` + hotfix PR #96 `b2aa1be`). |
| 016 | `016_add_extracted_document_pipeline_version.py` | adds a single nullable `pipeline_version` INTEGER column to `extracted_documents` (Phase 32A corrective, Problem B — derived-fact cache versioning). Existing rows are left NULL (treated as legacy/stale, never assumed compatible with the current parser/validator). Reversible, additive, backfill-free. |
| 017 | `017_add_extracted_fact_is_active.py` | adds a NOT NULL `is_active` BOOLEAN column (server default `true`) + index `ix_extracted_facts_document_active` to `extracted_facts` (Phase 32A corrective — active-vs-historical fact semantics, closing the MC table-loss regression where a partial revalidation had no honest way to supersede a document's prior fact set). Existing rows backfill to `true` (unchanged current behaviour). Reversible, additive. |
| 018 | `018_add_extracted_fact_scope.py` | adds three nullable columns `scope_type` / `scope_name` / `scope_key` + index `ix_extracted_facts_document_scope` to `extracted_facts` (private-use readiness PR-A — PERSIST FACT SCOPE). `ValidatedFact.scope` existed only in memory: the writer dropped it and the cache-reuse rebuild defaulted it to `None`, so a reused document handed the report layer segment facts with no scope — and an absent scope is the pipeline's implicit "this is the Group figure" convention. **Backfill: none.** No pre-existing row carries a recoverable scope signal, so every one stays NULL — unknown remains unknown; guessing `group` would manufacture the exact false Group attribution the column prevents. Reversible, additive, non-destructive. |

**Phase 32A Slice 6D adds migration `015` → head `015` — applied and
schema-verified on staging 2026-08-10 (`alembic current` = `015`).** It is reversible,
additive and backfill-free, and both new tables stay **unwritten** unless BOTH
`LLM_COUNCIL_ENABLED` and `LLM_FIELD_REVIEW_COUNCIL_ENABLED` are on (the feature
ships **default-OFF**), so with the flags off the DB is effectively unchanged even
after the migration is applied. Verified locally against PostgreSQL 16:
`alembic upgrade head` (014 → 015) → `alembic downgrade -1` (015 → 014) →
`upgrade head` again, all clean, with the downgrade dropping **only** the two new
tables and their six indexes. See the
`Deep Field Review (Phase 32A Slice 6D)` tables section below.

**Phase 32A Slice 5B.1 added migration `014` (on top of Slice 5A's `013`).**
Slice 5B.1 is **CLOSED + STAGING-VALIDATED** — `014` was applied and verified on
staging 2026-08-05. It is reversible, additive and backfill-free, and the new
`document_ingestion_attempts` table stays **unwritten** unless BOTH
`PRIMARY_DOCUMENT_INGESTION_ENABLED` and `REPORT_CITATION_PERSISTENCE_ENABLED` are
on. See the `Document Ingestion Attempts (Phase 32A Slice 5B.1)` tables section
below.

Migration `013` (Phase 32A Slice 5A) is reversible, additive and backfill-free,
and its two tables stay **unwritten** unless `PRIMARY_DOCUMENT_INGESTION_ENABLED`
is on (with the ingestion flag off the DB is effectively unchanged even after the
migration is applied). See the `Primary-Document Ingestion (Phase 32A Slice 5)`
tables section below.

**DB head = `012`** (before Slice 5's `013`). **Phase 32A Slice 3 (source/citation persistence + honest
reconciliation) adds NO migration — head stays `012`.** It persists into the
EXISTING `sources` / `citations` tables using columns that already exist:
`citations.report_id` / `agent_run_id` / `source_tier` / `data_quality` (002/003),
`reports.company_id` / `created_by_agent_run_id` (012 / earlier), and
`sources.content_hash` (002). Behind the OFF-by-default flag
`REPORT_CITATION_PERSISTENCE_ENABLED`, the company-analysis draft backfills
`citations.report_id` for its run (scoped by `agent_run_id`, idempotent via the
`report_id IS NULL` guard); the final report carries `company_id` +
`created_by_agent_run_id` as lineage; completed-agent council claim→evidence is
written as canonical `Source` + `Citation` rows (a synthesized `content_hash` on
the `Source` dedups url-less SEC/XBRL facts so re-runs never accumulate duplicates;
`citations.field_path` is prefixed `council:<agent>` so the loader can keep
council rows report-scoped while surfacing deterministic lineage rows). A DB
UNIQUE constraint on `sources`/`citations` was deliberately **not** added (the
nullable `url` / `content_hash` / `field_path` columns defeat a useful uniqueness
key and would break the ALTER on existing staging duplicates). **Deferred future
refactor:** a dedicated `evidence` / `claim_evidence_link` table + a Source
canonical-key unique constraint. **Legacy policy:** reports created before the
slice keep honest zero-count appendices (their citations were never linked and
their final reports were never lineage-connected — NOT force-backfilled, safely
unrecoverable); `reports.company_id = NULL` behavior stays explicit.

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

-- Phase 16 Final Report Generator columns
final_report_version        VARCHAR(20) NULLABLE   -- e.g. "16.0.0"
safety_validation_json      JSONB NULLABLE          -- SafetyValidationResult; blocks_approval=True if forbidden terms found
schema_validation_json      JSONB NULLABLE          -- schema validation errors and warnings
source_summary_json         JSONB NULLABLE          -- aggregated source/citation counts
scorecard_id                UUID FK → scorecards.id (SET NULL) NULLABLE

-- Phase 32A hotfix column (migration 012)
company_id                  UUID FK → companies.id (SET NULL) NULLABLE  -- company this report is about; enables company-scoped from-company selection

created_at                  TIMESTAMP WITH TIME ZONE
updated_at                  TIMESTAMP WITH TIME ZONE

INDEX: slug, status, review_status, report_type, published_at, scorecard_id, company_id
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

---

### Research Attractiveness Scoring (Phase 15)

**scorecards**
```
id                          UUID PK
company_id                  UUID NULLABLE FK → companies.id (SET NULL)
screening_candidate_id      UUID NULLABLE FK → screening_candidates.id (SET NULL)
report_id                   UUID NULLABLE FK → reports.id (SET NULL)
score_type                  VARCHAR(50) NOT NULL   "candidate_scoring" | "company_analysis_scoring"
overall_score               INTEGER NOT NULL        0–100 composite (T6/mock ≤ 30, T5 ≤ 60, T1/T2 ≤ 100)
internal_status             VARCHAR(100) NOT NULL   from ALLOWED_INTERNAL_STATUSES (research queue label only)
scores_json                 JSONB NULLABLE          {dimension_name: {score, explanation, evidence_used, missing_data, warnings}}
warnings_json               JSONB NULLABLE          list of warning strings
missing_data_json           JSONB NULLABLE          list of missing data field names
source_quality_summary_json JSONB NULLABLE          {source_tier, is_mock, quality_score, ...}
provider_name               VARCHAR(100) NULLABLE
created_at                  TIMESTAMP WITH TIME ZONE NOT NULL

INDEX: company_id, screening_candidate_id, report_id, score_type, overall_score DESC, created_at DESC
```

**10 scoring dimensions (all 0–100 integers):**

| Dimension | Weight | What it measures |
|---|---|---|
| `source_quality_score` | 20% | T1–T6 tier quality; T5/T6 produce warnings |
| `data_completeness_score` | 18% | Ratio of available vs expected fields |
| `theme_alignment_score` | 15% | Match against 6 investment themes |
| `business_quality_score` | 12% | Identity completeness (ticker, name, sector, country) |
| `financial_strength_score` | 12% | Financial data fields present |
| `valuation_readiness_score` | 10% | Readiness for future valuation work (not a valuation) |
| `growth_context_score` | 8% | Growth indicators in discovery reasons + sector |
| `catalyst_visibility_score` | 5% | Catalysts/triggers visible |
| `risk_penalty_score` | -20% | Source risk, mock data, missing data (subtracted) |

**ALLOWED_INTERNAL_STATUSES (research queue labels — never public recommendations):**

| Status | Meaning |
|---|---|
| `not_enough_data` | Insufficient data to score; further data collection required |
| `low_priority_research` | Low score; deprioritise for now |
| `needs_primary_sources` | T5/T6 only; T1/T2 validation required before progress |
| `ready_for_deeper_analysis` | Sufficient data quality for company-analysis workflow |
| `high_priority_for_human_review` | High score; admin should review for analysis pipeline |
| `reject_due_to_data_quality` | Data quality too poor to proceed |

**Forbidden values (never stored):**
`BUY`, `SELL`, `HOLD`, `WATCH`, `price_target`, `fair_value`, `upside_percent`

---

### Final Report Generator (Phase 16)

Phase 16 adds 5 columns to the existing **reports** table (migration 008) and does not create new tables.

**New reports columns (migration 008):**

| Column | Type | Description |
|---|---|---|
| `final_report_version` | VARCHAR(20) NULLABLE | Generator version, e.g. `"16.0.0"` |
| `safety_validation_json` | JSONB NULLABLE | Safety gate result; `blocks_approval=True` if forbidden language found |
| `schema_validation_json` | JSONB NULLABLE | Schema validation errors and warnings |
| `source_summary_json` | JSONB NULLABLE | Aggregated source/citation counts per section |
| `scorecard_id` | UUID FK → scorecards.id (SET NULL) NULLABLE | Links report to its Phase 15 scorecard |

**19 required report sections** (stored in `content_json`):`admin_disclaimer`, `executive_summary`, `company_identity`, `discovery_rationale`, `data_availability_summary`, `financial_snapshot`, `internal_scorecard`, `valuation_readiness`, `bull_case`, `bear_case`, `risk_analysis`, `source_quality_review`, `citation_validation_review`, `research_completeness_review`, `missing_information`, `committee_chair_summary`, `workflow_status`, `human_review_checklist`, `source_citation_appendix`

**Safety gate forbidden terms (never stored in report text):**
`BUY`, `SELL`, `HOLD`, `WATCH`, `price target`, `target price`, `fair value`, `intrinsic value`, `upside of`, `upside percentage`, `guaranteed return`, `will go up`, `will go down`

**Exempt field names** (scanned content but term allowed as meta-documentation):
`disallowed_outputs`, `blocked_methods`, `forbidden_terms_found`, `forbidden_terms`, `prohibited_outputs`

---

### Market Candidate Discovery (Phase 25)

Migration `010` creates two internal-only tables. No BUY/SELL/HOLD/WATCH labels,
price targets, fair values, or recommendations are ever stored. Every candidate
is `human_review_required=true` and `is_public=false`.

**`discovery_runs`** — one execution of a bounded, internal market scan.

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `status` | VARCHAR(50) | `pending` / `running` / `completed` / `completed_with_warnings` / `failed` / `cancelled` |
| `provider_name` | VARCHAR(50) | `free_real` (default) / `eodhd_free_real` / `mock` |
| `universe_source` | VARCHAR(50) | `curated_seed` / `manual_tickers` |
| `universe_count` / `processed_count` / `candidate_count` / `error_count` | INT | Run progress counters |
| `requested_tickers` / `warnings` / `config_json` / `safety_notes` | JSONB | Run inputs, warnings, config, safety metadata |
| `lookback_days` | INT | Price/catalyst lookback window |
| `human_review_required` | BOOL (default true) | Always true |
| `started_at` / `completed_at` / `created_at` / `updated_at` | TIMESTAMPTZ | |

**`discovery_candidates`** — a ranked internal research candidate.

| Group | Columns |
|---|---|
| Identity | `ticker`, `exchange`, `company_name`, `legal_name`, `sector`, `industry`, `country`, `lei`, `website` |
| Scoring (internal only) | `candidate_score`, `candidate_score_grade`, `rank`, `momentum_score`, `fundamentals_score`, `catalyst_score`, `source_quality_score`, `data_completeness_score`, `risk_penalty_score`, `labels_json`, `score_explanation` |
| Trend (T6) | `momentum_label`, `return_1m/3m/6m`, `pct_above_ma50/ma200` |
| Catalysts | `catalyst_coverage_status`, `latest_catalyst_date`, `positive_catalyst_count`, `high_strength_catalyst_count`, `press_release_event_count`, `news_event_count`, `filing_event_count`, `primary_or_regulator_event_count`, `aggregator_only_event_count` |
| Financial/market | `latest_close`, `market_cap_mln`, `enterprise_value_mln`, `pe_ratio`, `revenue_mln`, `revenue_growth_yoy_pct`, `net_income_mln`, `free_cash_flow_mln`, `total_debt_mln`, `cash_mln`, `latest_annual_fy` |
| Completeness/source | `source_quality`, `missing_info_count`, `blocking_gap_count`, `source_tiers_json`, `warnings_json`, `missing_sources_json`, `missing_fields_json`, `raw_signal_json`, `snapshot_json` |
| Workflow linkage | `analysis_report_id` (FK reports SET NULL), `agent_run_id` (FK agent_runs SET NULL) |
| Safety | `human_review_required` (true), `is_public` (false), `safety_valid`, `schema_valid`, `safety_notes` |

Constraint: `UNIQUE(discovery_run_id, ticker, exchange)`. Indexes on
`discovery_run_id`, `ticker`, `candidate_score`, `sector`, `candidate_score_grade`.

Note: `schema_valid` is expected to be `false` for generated reports at this
phase and is **not** a blocker for candidate creation.

### Thesis-to-Universe Discovery (Phase 27)

Migration `011` extends the two Phase 25 tables (no new tables) so a discovery
run can be driven by a natural-language **market thesis**. All added fields are
internal prioritization signals only — never a recommendation, price target,
fair value, or BUY/SELL/HOLD/WATCH label. Fully reversible.

**`discovery_runs`** — added columns:

| Column | Type | Description |
|---|---|---|
| `mode` | VARCHAR(20) NOT NULL default `ticker` | `ticker` (Phase 25 manual/curated) / `thesis` (segment-generated) |
| `thesis_text` | TEXT (nullable) | Raw admin thesis (NULL for ticker runs) |
| `parsed_thesis_json` | JSONB (nullable) | Structured parse: themes / sectors / industries / regions / countries / keywords / confidence / needs_narrowing |
| `universe_json` | JSONB (nullable) | Generated universe (`items[]`, `excluded[]` with reasons, `source_summary`) |

`universe_source` gains a `thesis_generated` value. New index
`ix_discovery_runs_mode`.

**`discovery_candidates`** — added columns:

| Column | Type | Description |
|---|---|---|
| `thesis_relevance_score` | FLOAT (nullable) | Pre-scan thesis match (0–100, internal only) |
| `combined_internal_score` | FLOAT (nullable) | Blend of thesis relevance + Phase 25 discovery signals (0–100, internal only) |
| `thesis_match_json` | JSONB (nullable) | Matched keywords, relevance reason, `internal_interest_label`, universe source/tier |

New index `ix_discovery_candidates_combined_score`. All thesis columns are NULL
for ticker runs.

### Phase 27.1B — no migration

Phase 27.1B (luxury/watch theme, sector taxonomy, supported themes,
company-name backfill fix) adds **no columns and no migration**. It reuses the
existing JSONB payloads:

| Column | Phase 27.1B additions |
|---|---|
| `discovery_runs.universe_json` | `items[]` now include curated luxury issuers (`UHR.SW`, `CFR.SW`, `MC.PA`, …); shape unchanged |
| `discovery_candidates.thesis_match_json` | `company_name`, `company_name_source`, `company_name_source_tier` |
| `discovery_candidates.raw_signal_json` | `identity.company_name` (resolved display name), `identity.company_name_source`, `identity.company_name_source_tier` |
| `discovery_candidates.missing_fields_json` | `fundamentals_not_sourced_non_us_exchange` for non-SEC venues (Phase 27.1A behaviour, now routinely exercised by the mostly-European luxury universe) |

`company_name_source` ∈ `provider_profile` | `curated_theme_registry` | `null`;
`company_name_source_tier` ∈ `T3_curated_reference_list` | `null`.

Attribution is decided by `data_coverage.profile_source` (a `not_sourced`
provider is never credited) **and** by value (a name equal to the curated
registry string is attributed to the registry) — not by whether the stored name
looks like a bare ticker. `ensure_company` seeds the row with the curated name
and the workflow echoes it back, so the placeholder test alone mis-credited
curated names to `provider_profile`; corrected after Phase 27.1B staging.

A curated display name is never attributed to SEC or a provider, and
`discovery_candidates.legal_name` is left exactly as the scan produced it (the
bare ticker when the provider sourced no profile) — a curated name is not
evidence of a legal name.

Existing rows are unaffected: absent keys simply read as `None`.

The `companies.name` column can now be **upgraded in place** when it holds a
bare-ticker stub (created by a discovery scan) and a curated name is available.
A name that is not a bare ticker is never overwritten.

---

### Primary-Document Ingestion (Phase 32A Slice 5)

> **Implemented — PR open, pending staging validation.** Migration `013`
> (`013_add_extracted_documents.py`, reversible / additive / backfill-free) creates
> two internal-only tables. Rows are only ever written when
> `PRIMARY_DOCUMENT_INGESTION_ENABLED` is on; with the flag off (default) the tables
> stay empty. No BUY/SELL/HOLD/WATCH labels, no valuations, no recommendations —
> extracted facts are research evidence that **always** requires human review.

`extracted_documents` records the lineage + status of ONE ingested issuer primary
document (annual report / registration document), deduped by `content_hash` — a
sha256 of the RAW fetched bytes (UNIQUE index). It carries the BOUNDED excerpts
(`excerpts_json`) produced for the document so a later report regeneration can
rebuild + reuse the extraction without re-fetching / re-extracting — never the full
document text or the raw table grid.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `content_hash` | str(64) | sha256 of raw fetched bytes; **UNIQUE** index (`ix_extracted_documents_content_hash`) — the document dedup key |
| `canonical_url` | str(2000) | credential-stripped canonical URL (never a signed/secret URL) |
| `provider` | str(100) | which connector/provider fetched it |
| `source_type` | str(50) | e.g. issuer annual report / registration document |
| `source_tier` | str(50) | evidence tier (e.g. `T1_primary_filing`) |
| `mime_type` | str(100) | |
| `title` | str(500) nullable | |
| `doc_date` | date nullable | |
| `period` | str(50) nullable | |
| `retrieved_at` | timestamptz | drives the reuse TTL (`PRIMARY_DOCUMENT_REUSE_TTL_HOURS`) |
| `extraction_method` | str(50) | native pdf / html / ocr |
| `page_count` | int nullable | |
| `status` | str(50) | `extracted` / `metadata_only` / `extraction_failed` |
| `company_id` | UUID FK → companies.id | `ON DELETE SET NULL`; index `ix_extracted_documents_company_id` |
| `agent_run_id` | UUID FK → agent_runs.id | `ON DELETE SET NULL`; index `ix_extracted_documents_agent_run_id` |
| `blob_path` | str(1000) nullable | **unused hook** — reserved for the deferred blob-storage document-body cache (ADR-014) |
| `excerpts_json` | JSONB nullable | a bounded JSON ARRAY of excerpt dicts (never full text / raw table grid); each carries its own `extraction_method`. **Phase 32A Slice 5B.2**: an OCR-recovered excerpt (`extraction_method: "ocr"`) persists through this SAME unchanged path once OCR has promoted the document to `extracted` — no schema change was needed for excerpts. **Still-open gap:** `PrimaryDocumentExtraction.tables` is not persisted anywhere — a validated fact's own `page_number`/`table_location` (on `extracted_facts`, below) is the only durable table provenance today. **Phase 32A corrective (cache/derivation correctness):** because the table grid is never cached, a revalidation whose active facts include a table-derived one can no longer be safely rebuilt from `excerpts_json` alone (see `pipeline_version` below) — `load_reusable_documents` instead performs ONE bounded full re-extraction from this row's own `canonical_url`. Persisting the bounded table grid itself (and sanitized OCR provider/model/version metadata) remains a follow-up that would let that path skip the re-fetch, tracked in ADR-016. |
| `pipeline_version` | int nullable | The extraction/parsing/validation pipeline version (`app.services.sources.extraction_pipeline_version.CURRENT_EXTRACTION_PIPELINE_VERSION`, currently **3** — migration 016 added the column, this corrective slice bumped 2→3) active when this row's active `extracted_facts` were produced. NULL = written before this column existed — treated as legacy/stale. On a version mismatch, `load_reusable_documents` classifies the document: if no active fact is table-derived, it RE-DERIVES facts from the reused `excerpts_json` under current-code semantics (no re-fetch); if a table-derived active fact exists, excerpts alone are insufficient, so it attempts one bounded full re-extraction from `canonical_url` instead. Either path, on success, ATOMICALLY supersedes the active `extracted_facts` set (see `is_active` below) before stamping this column current — a failed/incomplete re-extraction leaves this column stale rather than restamping from a partial result (the exact regression a live MC report hit under the old always-excerpts-only revalidation). |
| `created_at` / `updated_at` | timestamptz | |

`extracted_facts` stores the bounded primary facts parsed from a document, each
with provenance (page / table location), an extraction confidence, a validation
status and a human-review flag. Table/OCR-derived facts must clear stricter
validation (label/value/unit/period + table-column alignment + cross-field
arithmetic + cross-method agreement; OCR downgraded); anything short of the bar is
retained `excerpt_only` and is never a structured fact.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `extracted_document_id` | UUID FK → extracted_documents.id | `ON DELETE CASCADE`; index `ix_extracted_facts_extracted_document_id` |
| `label` | str(200) | |
| `value_numeric` | numeric nullable | |
| `value_text` | text nullable | |
| `unit` / `currency` / `scale` / `period` | str nullable | |
| `page_number` | int nullable | provenance |
| `table_location` | str(200) nullable | provenance (table/cell) |
| `extraction_method` | str(50) | native / table / ocr |
| `confidence` | float | |
| `validation_status` | str(50) | `validated` / `excerpt_only` / `rejected` |
| `needs_human_review` | bool (default true) | always true for extracted facts |
| `is_active` | bool (default true) | **Migration 017 (Phase 32A corrective).** True for the CURRENT active representation of this (document, label, period, scope); a document revalidation that completely re-derives its fact set flips the prior active rows to `false` (never deleted — audit/history preserved) and inserts the new set active. Every query feeding a current report filters `is_active = true`; index `ix_extracted_facts_document_active` on `(extracted_document_id, is_active)`. |
| `scope_type` | varchar(20) nullable | **Migration 018 (private-use readiness PR-A).** `'group'` \| `'segment'` \| NULL. The COARSE, decidable semantic the report layer branches on, so "is this the consolidated figure?" never depends on string-matching a label table at read time. NULL means UNKNOWN and is **never** coerced to `group` at write time. Derived by `app.services.sources.fact_scope.parse_scope`, the single vocabulary shared by the parser, the writer and every reader. |
| `scope_name` | varchar(200) nullable | The normalized as-found segment label (e.g. `Specialist Watchmakers`) for display and diagnostics. NULL for `group` and for unknown. |
| `scope_key` | varchar(220) nullable | Derived identity — `'group'` \| `'segment:<casefolded name>'` \| NULL — and part of a fact's dedupe/supersession IDENTITY alongside `(label, period, value)`. Without it a Group and a segment figure that share a value collapse into one row. Casefolded so label whitespace/casing drift cannot split one series in two; the distinct NAME is still preserved on `scope_name`. Index `ix_extracted_facts_document_scope` on `(extracted_document_id, scope_key)`. |
| `created_at` | timestamptz | |

ORM models: `ExtractedDocument` / `ExtractedFact` in
`apps/api/app/models/extracted_document.py`.

---

### Document Ingestion Attempts (Phase 32A Slice 5B.1)

> **Implemented — PR open, pending staging validation.** Migration `014`
> (`014_add_document_ingestion_attempts.py`, reversible / additive / backfill-free)
> creates one internal-only table. Rows are only ever written when BOTH
> `PRIMARY_DOCUMENT_INGESTION_ENABLED` and `REPORT_CITATION_PERSISTENCE_ENABLED`
> are on; with either flag off the writer issues no query and the table stays
> empty. No financial numbers, no valuations, no recommendations — this is
> ingestion telemetry, not evidence.

Slice 5A only wrote an `extracted_documents` row when a document reached
`status = 'extracted'`, so every FAILED attempt persisted nothing: a staging run
that tried documents across seven issuers left `extracted_documents` /
`extracted_facts` at 0/0 with no durable record of what was tried or why it
failed. `document_ingestion_attempts` is that record — one row per
`(company_id, agent_run_id, url_hash)` attempt, **updated in place** when the same
URL is re-attempted in the same run.

Bounded and secret-free by construction. **Never stored:** raw provider or
exception text, secrets / signed query strings (the URL is canonicalized +
credential-stripped before it is hashed or stored), exact HTTP status codes (only
the class), document bodies, extracted excerpts or OCR text.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `company_id` | UUID FK → companies.id nullable | `ON DELETE SET NULL`; index `ix_document_ingestion_attempts_company_id` |
| `agent_run_id` | UUID FK → agent_runs.id nullable | `ON DELETE SET NULL`; index `ix_document_ingestion_attempts_agent_run_id` |
| `canonical_url` | str(2000) | credential-stripped canonical URL (truncated, never rejected) |
| `url_hash` | str(64) | sha256 of `canonical_url`; index `ix_document_ingestion_attempts_url_hash` — signed-token variants of one document hash identically |
| `source_type` | str(50) | e.g. `company_ir_primary_document` |
| `source_tier` | str(50) | evidence tier (e.g. `T1_primary_filing`) |
| `doc_kind` | str(50) nullable | e.g. annual report / registration document / IR page |
| `discovery_strategy` | str(50) nullable | how the candidate URL was found |
| `attempted_at` | timestamptz | server default `now()` |
| `status` | str(50) | **CLOSED vocabulary**: `extracted`, `metadata_only`, `unsupported`, `encrypted`, `password_protected`, `malformed`, `rejected_security`, `timeout`, `extraction_failed` — an unrecognised status is SKIPPED, never stored. `discovered` and `fetched` are RESERVED members of the vocabulary that no writer currently emits (a candidate ranked out before a fetch produces no row in Slice 5B.1) |
| `failure_code` | str(50) nullable | **CLOSED, sanitized vocabulary**: `blocked_host`, `blocked_scheme`, `blocked_private_ip`, `blocked_redirect`, `redirect_limit`, `unsupported_content_type`, `http_client_error`, `http_server_error`, `fetch_timeout`, `extraction_timeout`, `not_a_pdf`, `encrypted_pdf`, `password_protected_pdf`, `malformed_pdf`, `scanned_no_text`, `empty_extraction`, `budget_exhausted`, `client_unavailable`, `missing_cik`, `conflicting_cik`, `malformed_accession`, `invalid_sec_url`, `no_primary_filing_document`, `preflight_budget_exhausted`, `ocr_document_too_large`, `ocr_page_limit_exceeded`, `ocr_timeout`, `ocr_provider_throttled`, `ocr_provider_error`, `ocr_malformed_result`, `ocr_low_confidence`, `ocr_budget_exhausted`, `unknown` — anything else is downgraded to `unknown` so raw provider text can never reach the DB. The six `*_cik`/`*_accession`/`*_sec_url`/`*_document`/`preflight_*` codes (Slice 5B.1 hotfix) cover a KNOWN candidate that never reached a network fetch — an unresolvable or conflicting SEC filer identity, a malformed accession, an unsafe filename, no selectable primary document, or the preflight time/attempt budget running out before this candidate's own fetch could start. The eight `ocr_*` codes (Phase 32A Slice 5B.2) cover a real-OCR attempt that did not produce usable content — each maps onto one of the THREE pre-existing statuses (`metadata_only` / `timeout` / `extraction_failed`), never a new status |
| `pinned` | bool nullable | tri-state: `true` = the connection was pinned to a pre-validated address (ADR-015); `false` = an honest "not pinned" (kill-switch off, or pinning unavailable); NULL = no fetch happened. Never claims pinning that did not occur. |
| `mime_type` | str(100) nullable | |
| `http_status_class` | str(10) nullable | `2xx` / `3xx` / `4xx` / `5xx` **only** — never the exact code |
| `extraction_method` | str(50) nullable | native pdf / html / ocr (NULL when nothing was extracted) |
| `page_count` | int nullable | |
| `content_hash` | str(64) nullable | sha256 of the raw bytes when a body was obtained — ties the attempt to its `extracted_documents` row without duplicating it |
| `fetch_ms` / `extraction_ms` / `total_ms` | int nullable | wall-clock telemetry |
| `created_at` / `updated_at` | timestamptz | |

Idempotency key: UNIQUE `uq_document_ingestion_attempts_run_url` on
`(company_id, agent_run_id, url_hash)`. PostgreSQL NULLs never collide inside a
UNIQUE constraint, so a NULL `company_id` / `agent_run_id` is not protected by the
constraint alone — the writer service **also** pre-queries for the existing row
and updates it in place (the constraint is the backstop, the pre-query is the
guarantee).

ORM model: `DocumentIngestionAttempt` in
`apps/api/app/models/document_ingestion_attempt.py`. Writer + bounded per-status
summary reader: `apps/api/app/services/document_ingestion_attempt_service.py`
(flush-only — the caller owns the commit).

---

### Deep Field Review (Phase 32A Slice 6D)

Migration `015`. Two tables recording a **comparative** review of the
already-completed analyses of 2+ candidates from ONE discovery run. This is a
THIRD council, distinct from the discovery council (candidate-list triage) and
the single-company council. **Nothing here is recomputed**: every stored summary
re-presents data already persisted on the candidate's report.

**`field_review_runs`** — one Deep Field Review job.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `discovery_run_id` | UUID FK → discovery_runs.id | `ON DELETE CASCADE`; index `ix_field_review_runs_discovery_run_id` |
| `status` | str(50) | **CLOSED vocabulary**: `pending`, `running`, `completed`, `completed_with_warnings`, `failed`, `insufficient_candidates`. Index `ix_field_review_runs_status` |
| `included_candidate_count` | int | how many candidates were actually compared |
| `missing_candidate_count` | int | how many could NOT be compared (all of them are recorded — see below) |
| `llm_used` | bool | honest: `true` only when a client really ran the council |
| `council_version` / `provider` / `model` | str nullable | never the Azure *deployment* name |
| `agents_completed` / `agents_failed` | int | honest per-run tallies |
| `field_quality` | str(20) nullable | `strong` \| `adequate` \| `thin` \| `failed` — an internal field-quality label, **never a rating** |
| `safety_valid` | bool nullable | `false` when the defensive re-scan flagged the payload (flagged, never silently stripped) |
| `review_json` | JSONB nullable | the full safety-scanned result (all eight agents' outputs, the three priority buckets, the disclaimer). **Never raw prompts or completions** |
| `warnings_json` | JSONB nullable | citation/safety issue notes |
| `error` | str(200) nullable | short, safe reason code only — never a raw exception string |
| `human_review_required` | bool | server default `true`; nothing here is publishable |
| `started_at` / `completed_at` / `created_at` / `updated_at` | timestamptz | index `ix_field_review_runs_created_at` |

**`field_review_candidate_summaries`** — one row per candidate **considered**,
included **or** excluded. An excluded candidate is never silently dropped
(CLAUDE.md rule 8: rejected cases are learning data).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `field_review_run_id` | UUID FK → field_review_runs.id | `ON DELETE CASCADE`; index `ix_field_review_candidate_summaries_run_id` |
| `discovery_candidate_id` | UUID FK → discovery_candidates.id nullable | `ON DELETE SET NULL` — research history survives a candidate deletion. Constraint name shortened to `fk_field_review_summaries_candidate_id_discovery_candidates` (the fully symmetrical name is 69 chars; PostgreSQL rejects identifiers over 63). Index `ix_field_review_candidate_summaries_candidate_id` |
| `report_id` | UUID FK → reports.id nullable | `ON DELETE SET NULL`; index `ix_field_review_candidate_summaries_report_id` |
| `citation_ref` | str(20) | the id the council cited this company by (`F1`, `F2`, …); excluded candidates get an `X`-prefixed ref so they can never collide with a cited company id |
| `ticker` / `exchange` | str nullable | |
| `included` | bool | server default `false` |
| `exclusion_reason` | str(50) nullable | **CLOSED vocabulary**: `no_analysis_run`, `report_deleted`, `draft_only`, `not_schema_valid`, `over_company_cap`. NULL when `included` |
| `data_provenance` | str(20) nullable | `real` \| `mock` \| `mixed` \| `unknown`, carried from the report — never guessed. A non-`real` company is **included with a caveat**, never dropped |
| `priority_tier` | str(50) nullable | **CLOSED vocabulary**: `strongest_candidates`, `second_tier`, `blocked_insufficient_evidence` — internal research buckets, **never** BUY/SELL/HOLD/WATCH |
| `summary_json` | JSONB nullable | the bounded `FieldReviewCompanySummary` actually sent to the council (NULL for an excluded candidate) |
| `created_at` | timestamptz | |

Uniqueness: UNIQUE `uq_field_review_candidate_summary_run_ref` on
`(field_review_run_id, citation_ref)` — one company can occupy a given citation
id at most once per review.

**Input linkage rule (load-bearing):** a candidate's report is resolved through
`discovery_candidates.analysis_report_id` **only**. There is deliberately **no**
"latest report for this company_id" fallback — that would resurrect the
from-company scoping bug fixed earlier in Phase 32A and could silently substitute
a report generated for a *different* run of the same company.

ORM models: `FieldReviewRun`, `FieldReviewCandidateSummary` in
`apps/api/app/models/field_review.py`. Resolution + async job orchestration:
`apps/api/app/services/field_review_service.py`.

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
