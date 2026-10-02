"""Web documents in the Research Corpus — open-web W3 (spec §12.2, §26.3).

WHAT THIS CARRIES
=================
* ``research_documents.subject_scope`` — what a document is about when it is not one
  company's (``company | theme | industry | macro``), plus a PARTIAL
  unique index on ``document_key WHERE company_id IS NULL``: the existing
  ``(company_id, document_key)`` index never deduplicates a company-less row, because
  NULLs are distinct.
* ``research_document_subjects`` — a new table: which companies/entities a document is
  about or mentions (an article about three companies is one document with three
  subject rows), with the match confidence, method, brand scope and the chunk the
  mention occurs in — and which THEMES it serves (``relation='theme'`` rows carrying
  ``theme_key``: one document is reused by many themes). A unique index over the row
  identity (NULLs coalesced) makes concurrent writers idempotent.
* ``research_document_versions``: ``web_fetch_attempt_id`` (FK ``web_fetch_attempts``,
  SET NULL), ``use_constraint``, ``injection_suspect``, ``simhash`` (BIGINT),
  ``origin_key``, ``published_at_source`` — the spec's list — plus ``source_class``
  (the §13.1 class the retrieval filter reads) and ``web_extractor_version``.
* ``research_leads``: ``web_search_result_id`` (FK SET NULL) and
  ``research_document_version_id`` (FK SET NULL) — an ``ev:x:`` id resolves to a
  stored version.

ADDITIVE ONLY
=============
Every new column is nullable with no default and no backfill; no existing column is
altered or dropped; every new FK is ``ON DELETE SET NULL`` except the subjects table's
own composition FK to its document (CASCADE, the same rule versions follow). Downgrade
drops exactly what upgrade added, in reverse.

THE ONE THING THAT CAN FAIL
===========================
The partial unique index cannot be built while two company-less documents share a
``document_key``. Nothing before W3 writes such pairs on purpose (the writer looks the
key up first), but a race could have. Upgrade checks first and stops with a message
naming the problem rather than a bare constraint error.

REVISION
========
Numbered 044 because 043 is taken by the report-reconciliation branch.
``down_revision`` points at 042 TEMPORARILY and is re-pointed at merge.

DEPLOY ORDER
============
The W3 ORM maps these columns, so apply 044 BEFORE deploying the code (the V3.19
pattern). With ``V3_WEB_CORPUS_INGEST_ENABLED`` off nothing writes them.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "044"
# re-pointed to 043 (report reconciliation) at merge
down_revision: str | None = "042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_TABLE = "research_document_subjects"

#: ``(table, column name, type)`` added by this migration, in order.
COLUMNS: tuple[tuple[str, str, sa.types.TypeEngine], ...] = (
    ("research_documents", "subject_scope", sa.String(20)),
    ("research_document_versions", "web_fetch_attempt_id", sa.Uuid(as_uuid=True)),
    ("research_document_versions", "use_constraint", sa.String(40)),
    ("research_document_versions", "injection_suspect", sa.Boolean()),
    ("research_document_versions", "simhash", sa.BigInteger()),
    ("research_document_versions", "origin_key", sa.String(255)),
    ("research_document_versions", "published_at_source", sa.String(20)),
    ("research_document_versions", "source_class", sa.String(40)),
    ("research_document_versions", "web_extractor_version", sa.Integer()),
    ("research_leads", "web_search_result_id", sa.Uuid(as_uuid=True)),
    ("research_leads", "research_document_version_id", sa.Uuid(as_uuid=True)),
)

#: ``(fk name, table, column, target table)``. All ``ON DELETE SET NULL``.
FOREIGN_KEYS: tuple[tuple[str, str, str, str], ...] = (
    (
        "fk_research_document_versions_web_fetch_attempt_id",
        "research_document_versions",
        "web_fetch_attempt_id",
        "web_fetch_attempts",
    ),
    (
        "fk_research_leads_web_search_result_id",
        "research_leads",
        "web_search_result_id",
        "web_search_results",
    ),
    (
        "fk_research_leads_research_document_version_id",
        "research_leads",
        "research_document_version_id",
        "research_document_versions",
    ),
)

#: ``(index name, table, columns)`` — non-unique.
INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "ix_research_document_versions_web_fetch_attempt_id",
        "research_document_versions",
        ("web_fetch_attempt_id",),
    ),
    (
        "ix_research_leads_research_document_version_id",
        "research_leads",
        ("research_document_version_id",),
    ),
    ("ix_research_document_subjects_document_id", NEW_TABLE, ("research_document_id",)),
    ("ix_research_document_subjects_company_id", NEW_TABLE, ("company_id",)),
    ("ix_research_document_subjects_theme_key", NEW_TABLE, ("theme_key",)),
)

#: The subject identity, NULLs coalesced (review F10). Mirrors
#: ``app.models.research_document.SUBJECT_IDENTITY_SQL``.
SUBJECT_UNIQUE_INDEX = "ux_research_document_subjects_identity"
SUBJECT_IDENTITY_SQL: tuple[str, ...] = (
    "research_document_id",
    "coalesce(company_id, '00000000-0000-0000-0000-000000000000')",
    "coalesce(legal_entity_id, '00000000-0000-0000-0000-000000000000')",
    "relation",
    "coalesce(scope_key, '')",
    "coalesce(theme_key, '')",
)

PARTIAL_UNIQUE_INDEX = "ix_research_documents_companyless_key"


def upgrade() -> None:
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            "SELECT count(*) FROM (SELECT document_key FROM research_documents "
            "WHERE company_id IS NULL GROUP BY document_key HAVING count(*) > 1) d"
        )
    ).scalar()
    if duplicates:
        raise RuntimeError(
            f"{duplicates} document_key value(s) are shared by more than one company-less "
            "research_documents row; merge or re-attribute them before applying 044 "
            "(the partial unique index cannot be built over duplicates)."
        )

    for table, name, type_ in COLUMNS:
        op.add_column(table, sa.Column(name, type_, nullable=True))
    for fk_name, table, column, target in FOREIGN_KEYS:
        op.create_foreign_key(fk_name, table, target, [column], ["id"], ondelete="SET NULL")

    op.create_table(
        NEW_TABLE,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("research_document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("legal_entity_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("relation", sa.String(20), nullable=False),
        sa.Column("confidence", sa.String(30), nullable=True),
        sa.Column("method", sa.String(40), nullable=False),
        sa.Column("scope_key", sa.String(220), nullable=True),
        sa.Column("evidence_chunk_id", sa.String(120), nullable=True),
        sa.Column("theme_key", sa.String(120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["research_document_id"],
            ["research_documents.id"],
            name="fk_research_document_subjects_document_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_research_document_subjects_company_id_companies",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entities.id"],
            name="fk_research_document_subjects_legal_entity_id",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "relation IN ('primary', 'mentioned', 'competitor', 'customer', 'supplier', "
            "'theme')",
            name="ck_research_document_subjects_relation",
        ),
        sa.CheckConstraint(
            "(relation = 'theme') = (theme_key IS NOT NULL)",
            name="ck_research_document_subjects_theme_key",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence IN "
            "('exact_identifier', 'domain', 'name_context', 'name_only')",
            name="ck_research_document_subjects_confidence",
        ),
    )
    for name, table, columns in INDEXES:
        op.create_index(name, table, list(columns))
    op.create_index(
        SUBJECT_UNIQUE_INDEX,
        NEW_TABLE,
        [sa.text(e) if "(" in e else e for e in SUBJECT_IDENTITY_SQL],
        unique=True,
    )
    op.create_index(
        PARTIAL_UNIQUE_INDEX,
        "research_documents",
        ["document_key"],
        unique=True,
        postgresql_where=sa.text("company_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(PARTIAL_UNIQUE_INDEX, table_name="research_documents")
    op.drop_index(SUBJECT_UNIQUE_INDEX, table_name=NEW_TABLE)
    for name, table, _columns in reversed(INDEXES):
        op.drop_index(name, table_name=table)
    op.drop_table(NEW_TABLE)
    for fk_name, table, _column, _target in reversed(FOREIGN_KEYS):
        op.drop_constraint(fk_name, table, type_="foreignkey")
    for table, name, _type in reversed(COLUMNS):
        op.drop_column(table, name)
