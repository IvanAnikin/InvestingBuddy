"""The production corpus search index — V3.4 Slice 4.9.

PURPOSE
=======
Make `research_document_chunks` searchable **inside PostgreSQL**, which is what
[ADR-047](../../../docs/DECISIONS.md) chose over a managed search service. Two
independent pieces:

1. **The lexical leg**, which needs no extension at all: a GIN expression index over
   ``to_tsvector('simple', text)``. Every metadata filter then applies in the *same*
   SQL statement as the ranking, which is the property that makes a scope filter
   trustworthy rather than eventually consistent.
2. **The index state and the semantic leg**, as four nullable columns.

WHY AN EXPRESSION INDEX AND NOT A `tsvector` COLUMN
==================================================
A stored generated column would be the faster read, and it would also be a
``TSVECTOR`` in the ORM — which the sqlite database every unit test builds cannot
express. The choice was between a physical detail that breaks the test suite and one
that does not, for a difference that is a recomputation over rows the index has
already narrowed. An expression index is, precisely, an index: no column, no ORM
drift, no dialect problem.

`'simple'` rather than `'english'`, for the reason `search/fusion.py` already argues:
in a financial corpus the exact token usually deserves to win. ``FY2025``, ``PNDORA``
and ``mRNA-1273`` survive `'simple'` intact. A stemmed leg is an ADDITIONAL expression
index later, never a replacement for this one.

WHY `indexed_at` IS NOT A DELETE
================================
For every other backend, "remove this version from the index" is a deletion. Here the
chunk rows **are** the corpus: deleting them would destroy the spans old citations
resolve against. So de-indexing sets ``indexed_at = NULL`` and the search reads only
rows where it is set. A superseded derivation's chunks stay citable and stop being
retrievable, which is exactly the distinction reprocessing needs (CLAUDE.md rule 15).

WHY THE EMBEDDING IS JSONB
==========================
`pgvector` is **not installed** on the PostgreSQL this project runs — verified, not
assumed: ``SELECT count(*) FROM pg_available_extensions WHERE name='vector'`` returns
0. A ``vector`` column would make this migration unrunnable rather than make one
feature unavailable. ADR-053 records the amendment: enabling the extension later adds
a typed column populated FROM this one, which is an additive migration over data
already held rather than a re-embedding.

ONE CHECK
=========
``ck_research_document_chunks_embedding_is_attributed`` — an embedding with no model
cannot be compared with any other, because two models' vectors share a dimension and
nothing else. Without the constraint that is a storable mistake whose symptom is
silently wrong similarity rather than an error.

ADDITIVE ONLY: four nullable columns, one CHECK, two indexes. Nothing writes to any of
it unless ``V3_CORPUS_ENABLED`` is on, which is off by default.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers
revision: str = "032"
down_revision: str | None = "031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "research_document_chunks"
_FTS_INDEX = "ix_research_document_chunks_fts"


def upgrade() -> None:
    op.add_column(
        _TABLE, sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(_TABLE, sa.Column("embedding_json", JSONB, nullable=True))
    op.add_column(
        _TABLE, sa.Column("embedding_model", sa.String(length=80), nullable=True)
    )
    op.add_column(_TABLE, sa.Column("embedding_dim", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_research_document_chunks_embedding_is_attributed",
        _TABLE,
        "embedding_json IS NULL OR "
        "(embedding_model IS NOT NULL AND embedding_dim IS NOT NULL "
        "AND embedding_dim > 0)",
    )
    op.create_index(
        "ix_research_document_chunks_indexed_at",
        _TABLE,
        ["indexed_at", "company_id"],
    )
    # The lexical leg. Created with raw DDL because it indexes an EXPRESSION, and
    # `to_tsvector` needs its configuration named explicitly to be immutable.
    op.execute(
        f"CREATE INDEX {_FTS_INDEX} ON {_TABLE} "
        "USING GIN (to_tsvector('simple', text))"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_FTS_INDEX}")
    op.drop_index("ix_research_document_chunks_indexed_at", table_name=_TABLE)
    op.drop_constraint(
        "ck_research_document_chunks_embedding_is_attributed", _TABLE, type_="check"
    )
    op.drop_column(_TABLE, "embedding_dim")
    op.drop_column(_TABLE, "embedding_model")
    op.drop_column(_TABLE, "embedding_json")
    op.drop_column(_TABLE, "indexed_at")
