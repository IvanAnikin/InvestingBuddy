"""Independent research coverage index — V3.21 slice 21.7 (spec §8, ADR-114).

ONE CHANGE
==========
``research_coverage_pages`` — one row per research-provider page URL: Edison Group's
``/equity/<company>/`` pages and ``/research/<note>/`` notes from its public sitemap
(refreshed weekly), and Redeye / Research Tree / mwb research pages found by a
host-restricted web search (at most one per company per 30 days). Columns: provider,
url (unique), page_kind (``equity`` | ``note``), company_slug, name_key (normalised),
title, lastmod, first_seen_at, last_seen_at, discovered_via (``sitemap`` | ``search``).
No page body and no claim is held.

ADDITIVE, REVERSIBLE: downgrade drops the table. The index is rebuilt by the next weekly
refresh, so dropping it loses no research history.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "064"
down_revision: str | None = "063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_coverage_pages",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("page_kind", sa.String(16), nullable=False),
        sa.Column("company_slug", sa.Text(), nullable=True),
        sa.Column("name_key", sa.String(200), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("lastmod", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("discovered_via", sa.String(16), nullable=False),
        sa.UniqueConstraint("url", name="uq_research_coverage_pages_url"),
        sa.CheckConstraint("page_kind IN ('equity', 'note')",
                           name="ck_research_coverage_pages_page_kind"),
        sa.CheckConstraint("discovered_via IN ('sitemap', 'search')",
                           name="ck_research_coverage_pages_discovered_via"),
    )
    op.create_index("ix_research_coverage_pages_name_key", "research_coverage_pages",
                    ["name_key"])
    op.create_index("ix_research_coverage_pages_provider_last_seen",
                    "research_coverage_pages", ["provider", "last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_research_coverage_pages_provider_last_seen",
                  table_name="research_coverage_pages")
    op.drop_index("ix_research_coverage_pages_name_key", table_name="research_coverage_pages")
    op.drop_table("research_coverage_pages")
