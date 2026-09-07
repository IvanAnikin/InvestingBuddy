"""Add the raw-artifact table — V3.1 Slice 1.1.

PURPOSE
=======
One row per distinct set of retrieved bytes, addressed by SHA-256, recording
where those bytes live and what policy governs them. It is what turns
``ExtractedDocument.blob_path`` — a column that has existed since migration 013
and has always been NULL — into a real retrieval path.

WHY IT MATTERS THAT THE BYTES SURVIVE
=====================================
InvestingBuddy's parsers improve constantly: the extraction pipeline version is
at 15, and several past correctives (a two-column PDF layout fix, a borderless
five-year table reconstructor, a gutter-threshold recalibration) could only be
applied by RE-FETCHING documents that may no longer be online. Retaining the raw
bytes makes re-extraction a local, deterministic operation.

ADDITIVE ONLY. Creates one table and touches nothing that exists, so code on
``release/v2-current`` runs unchanged against a database with this applied.
Nothing writes to it unless ``V3_CORPUS_ENABLED`` is on, which is off by default.

NULLABILITY IS A STATEMENT
==========================
``storage_key`` NULL means the bytes are NOT retained — policy forbade it, or a
retention sweep removed them. ``storage_backend`` is NOT NULL and carries
``'none'`` in that case, so "deliberately not retained" is always distinguishable
from "written by code that forgot to record where". The lineage row itself is
never deleted: a citation must keep resolving to its canonical URL after the
bytes are gone.

``retention_expires_at`` NULL means "no TTL configured". OPEN DECISION #12 (raw
page and document retention) is user-owned and still open, so a default expiry
is deliberately NOT encoded here — the column exists so the decision can be
applied later without a migration.

INDEXES, AND WHY EACH ONE EXISTS
================================
* ``ix_research_artifacts_content_hash`` (UNIQUE) — the identity. Deduplication
  is enforced by the database, not by a check-then-insert that two concurrent
  ingestions can both pass.
* ``ix_research_artifacts_retention_expires_at`` — "what is due for expiry" is the
  only scheduled question this table will ever be asked, and it must not become a
  sequential scan over every artifact ever held.

NO FOREIGN KEYS, DELIBERATELY
=============================
An artifact is bytes. Which company, run or report it belongs to is a property of
the *document* built on it (Slice 1.2). Putting a ``company_id`` here would make
the same bytes serving two documents unrepresentable, and would put research
history one ``ondelete`` away from disappearing (CLAUDE.md rule 15).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "021"
down_revision: str | None = "020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_artifacts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("storage_backend", sa.String(length=30), nullable=False),
        sa.Column("storage_key", sa.String(length=200), nullable=True),
        sa.Column("access_class", sa.String(length=30), nullable=False),
        sa.Column(
            "policy_stored", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "policy_indexed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "policy_external_model",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("policy_quoted", sa.String(length=20), nullable=False),
        sa.Column(
            "policy_retained_long_term",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bytes_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "seen_count", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_research_artifacts_content_hash",
        "research_artifacts",
        ["content_hash"],
        unique=True,
    )
    op.create_index(
        "ix_research_artifacts_retention_expires_at",
        "research_artifacts",
        ["retention_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_research_artifacts_retention_expires_at", table_name="research_artifacts")
    op.drop_index("ix_research_artifacts_content_hash", table_name="research_artifacts")
    op.drop_table("research_artifacts")
