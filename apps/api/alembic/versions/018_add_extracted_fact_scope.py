"""add extracted_facts scope_type / scope_name / scope_key

Revision ID: 018
Revises: 017
Create Date: 2026-08-25

Private-use production readiness, Phase PR-A — PERSIST FACT SCOPE.

``ValidatedFact.scope`` has existed in memory since the Phase 32A corrective
slice, and the report layer already refuses to promote a segment-scoped figure
into a canonical Group slot. But scope was never a COLUMN: it survived only for
as long as the freshly-extracted artifact stayed in memory.

Concretely, before this migration:

  * ``extracted_document_service._persist_validated_facts`` wrote every other
    field of a ``ValidatedFact`` and silently dropped ``scope``;
  * ``_rebuild_artifact`` (the cache-reuse / revalidation fast path) rebuilt
    ``ValidatedFact`` rows with ``scope`` defaulted to ``None``.

So a document reused from cache handed the connector layer segment facts with
no scope at all — and because an ABSENT scope is the pipeline's long-standing
implicit "this is the Group figure" convention, a Specialist Watchmakers
operating profit could be promoted into the Group slot on any cache-reused run.
The fresh path was correct; the persisted path was not. That is the class of
contradiction (``SCOPE_CONTRADICTION``) this readiness campaign exists to make
unrepresentable.

Three additive, nullable columns:

  ``scope_type``  'group' | 'segment' | NULL. The COARSE semantic the report
                  layer branches on, so "is this the consolidated figure?" is a
                  decidable question that does not depend on string-matching a
                  label table at read time. NULL means UNKNOWN and is never
                  coerced to 'group' at write time.
  ``scope_name``  The normalized as-found segment label (e.g. 'Specialist
                  Watchmakers') for display and diagnostics. NULL for group and
                  for unknown.
  ``scope_key``   Derived identity: 'group' | 'segment:<casefolded name>' |
                  NULL. This is what fact identity / dedupe / supersession
                  compare on, so two genuinely different segments never
                  collapse into one another and one segment never splits in two
                  because of label whitespace/casing drift.

BACKFILL: none. No scope was ever persisted, so no pre-existing row carries a
recoverable in-band signal. Every existing row stays NULL — unknown remains
unknown. Guessing 'group' for legacy rows would manufacture exactly the false
Group attribution this column exists to prevent.

Purely additive, non-destructive, reversible. Existing rows keep behaving as
they already do (an absent scope on the FRESH path retains the implicit-Group
convention); it is only the PERSISTED path that stops losing information.
Because the interpretation of persisted facts changes, the extraction pipeline
version is bumped alongside this migration so legacy rows are revalidated under
current semantics rather than trusted unchanged.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "018"
down_revision: str | None = "017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "extracted_facts",
        sa.Column("scope_type", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "extracted_facts",
        sa.Column("scope_name", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "extracted_facts",
        sa.Column("scope_key", sa.String(length=220), nullable=True),
    )
    # Supersession + representative selection query the active fact set of one
    # document and then group by scope; this keeps that path indexed.
    op.create_index(
        "ix_extracted_facts_document_scope",
        "extracted_facts",
        ["extracted_document_id", "scope_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_extracted_facts_document_scope", table_name="extracted_facts")
    op.drop_column("extracted_facts", "scope_key")
    op.drop_column("extracted_facts", "scope_name")
    op.drop_column("extracted_facts", "scope_type")
