"""Record WHICH BUDGET a parse ran under — V3.1 Slice 1.7.

PURPOSE
=======
``research_document_derivations`` already records the pipeline version a parse ran
under. It does not record how much of the document that parse was allowed to
read, and on the live path the answer is "40 pages of 169".

That gap makes reprocessing pointless in the case that matters most. Re-running
the same parser under a larger page budget produces a genuinely different and
better derivation — but ``(version, pipeline_version)`` is unique, so the second
run would find the first and skip. The document would stay at a quarter of itself
forever, and the whole argument for retaining raw bytes would go unrealised.

``extraction_profile`` closes that. The pages a parse was allowed to open are part
of what the parse IS:

* ``live`` — the caps the request path uses (``primary_document_max_pdf_pages``,
  40, plus a targeted 12-page supplemental pass). Bounded by the gunicorn worker
  timeout, and that bound is load-bearing: it drifted once and cost six outages.
* ``deep`` — the caps a REPROCESSING run uses, from the retained bytes, off the
  live request path, where no request is waiting and no worker heartbeat is at
  risk.

``extraction_pipeline_version``'s own note says a change to an extraction cap or
budget that lets the extractor reach content it previously never read makes a row
genuinely INCOMPLETE rather than merely under-interpreted. This column is that
statement made durable per row, rather than requiring a global version bump every
time a budget differs.

WHAT THIS MIGRATION DOES
========================
1. adds ``extraction_profile`` NOT NULL, server default ``'live'``;
2. replaces the unique index ``(version_id, pipeline_version)`` with
   ``(version_id, pipeline_version, extraction_profile)``.

NOT NULL IS SAFE HERE, AND THAT IS NOT THE GENERAL RULE
=======================================================
The migration plan's additive-only rule exists so ``release/v2-current`` code runs
unchanged against a migrated database. V2 code cannot be affected by this column:
``research_document_derivations`` does not exist in V2 at all — it was created by
migration 023, on this branch, and has never been applied anywhere. The server
default also means the column is populated for any row that could exist.

Replacing an index is likewise not a compatibility concern for a table no
deployed code reads.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "025"
down_revision: str | None = "024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_document_derivations",
        sa.Column(
            "extraction_profile",
            sa.String(length=30),
            nullable=False,
            server_default="live",
        ),
    )
    op.drop_index(
        "ix_research_document_derivations_version_pipeline",
        table_name="research_document_derivations",
    )
    op.create_index(
        "ix_research_document_derivations_version_pipeline",
        "research_document_derivations",
        ["research_document_version_id", "pipeline_version", "extraction_profile"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_document_derivations_version_pipeline",
        table_name="research_document_derivations",
    )
    op.create_index(
        "ix_research_document_derivations_version_pipeline",
        "research_document_derivations",
        ["research_document_version_id", "pipeline_version"],
        unique=True,
    )
    op.drop_column("research_document_derivations", "extraction_profile")
