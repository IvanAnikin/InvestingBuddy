"""Research question graph, finding ownership, thesis lineage — V3.18.

WHAT THIS CARRIES
=================
Every schema change V3.18 (Professional Research Depth) needs, in ONE migration, so that
reaching production costs one human step rather than four.

* ``research_questions`` — the flat question list becomes a **graph**: a domain, an owner
  role, why the question matters, what it depends on, the evidence contract that decides
  when it is answered, the deterministic search intents, the contract's outcome, the
  reason it stayed open, and an acquisition log that answers *"why did the agent search
  for this source?"* and *"why did it stop?"*.
* ``research_findings`` — a finding gets an **owner topic**, so the same observation
  stops being restated by six specialists: ``topic_key``, ``domain``, the normalised
  ``claim_key`` used to detect a restatement, and the findings it references instead.
* ``research_gaps`` — ``knowledge_state``: whose gap it is. "InvestingBuddy has not
  acquired X" and "the company does not disclose X" are different statements.
* ``research_runs`` — ``thesis_json``: the discovery thesis that caused the research, so
  deep research can evaluate the reason the company was selected.
* ``research_leads`` — what verification actually matched (``matched_excerpt``), and the
  metric / unit context the claim was about (``claimed_metric``, ``claimed_geography``).
* ``macro_series`` — ``commodity``, so an external quantitative series (a benchmark
  price, world mine production) is queryable by the material it describes.

ADDITIVE ONLY
=============
Nullable columns and two non-unique indexes. No column altered, renamed or dropped; no
constraint changed; no backfill; no default that rewrites a row. NULL correctly means
"written before V3.18" on every one of them. ``resolution_status`` and its CHECK are left
exactly as they are — the contract outcome gets a column of its own rather than a new
member of a constrained vocabulary, because altering a CHECK on a live table is the only
part of this that could have been not-additive.

Downgrade drops only what this added.

DEPLOY ORDER
============
This file ships with **no ORM change**, deliberately. Code that does not know about a
nullable column is unaffected by it, so this migration may be applied to a database at
any time before the slices that map the columns are deployed. The reverse order — ORM
columns on a schema that lacks them — fails every query on the table, which is the
outage V3's dark deployment was reordered to avoid.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers
revision: str = "041"
down_revision: str | None = "040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: ``(table, column name, type)`` in creation order. Downgrade walks it backwards, so the
#: two can never disagree about what this migration owns. EVERY column is nullable with
#: no default — `_column` is the only place one is built, so that cannot drift per row.
_COLUMNS: tuple[tuple[str, str, sa.types.TypeEngine | type], ...] = (
    ("research_questions", "domain", sa.String(40)),
    ("research_questions", "why_it_matters", sa.String(1000)),
    ("research_questions", "owner_role", sa.String(60)),
    ("research_questions", "depends_on_json", JSONB),
    ("research_questions", "required_metrics_json", JSONB),
    ("research_questions", "evidence_contract_json", JSONB),
    ("research_questions", "search_intents_json", JSONB),
    ("research_questions", "contract_status", sa.String(30)),
    ("research_questions", "contract_detail_json", JSONB),
    ("research_questions", "unresolved_reason", sa.String(40)),
    ("research_questions", "acquisition_log_json", JSONB),
    ("research_findings", "topic_key", sa.String(160)),
    ("research_findings", "domain", sa.String(40)),
    ("research_findings", "claim_key", sa.String(240)),
    ("research_findings", "references_finding_ids_json", JSONB),
    ("research_findings", "source_kinds_json", JSONB),
    ("research_gaps", "knowledge_state", sa.String(50)),
    ("research_runs", "thesis_json", JSONB),
    ("research_leads", "matched_excerpt", sa.String(800)),
    ("research_leads", "claimed_metric", sa.String(120)),
    ("research_leads", "claimed_geography", sa.String(80)),
    ("macro_series", "commodity", sa.String(60)),
)

_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_research_findings_run_topic", "research_findings", ("research_run_id", "topic_key")),
    ("ix_research_questions_run_domain", "research_questions", ("research_run_id", "domain")),
)


def _column(name: str, type_: "sa.types.TypeEngine | type") -> sa.Column:
    return sa.Column(name, type_, nullable=True)


def upgrade() -> None:
    for table, name, type_ in _COLUMNS:
        op.add_column(table, _column(name, type_))
    for name, table, columns in _INDEXES:
        op.create_index(name, table, list(columns))


def downgrade() -> None:
    for name, table, _columns in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
    for table, name, _type in reversed(_COLUMNS):
        op.drop_column(table, name)
