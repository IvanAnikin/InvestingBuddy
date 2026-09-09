"""Company classification provenance — V3.15.

WHAT WAS WRONG
==============
The platform could classify Moderna and could not act on it. The SEC returns SIC ``2836``
on every submissions request the platform already makes; ``enrich_company_profile``
turned that into a sector; and the answer was written into a report snapshot and then
dropped. ``companies.sector`` stayed NULL, the playbook matcher read ``companies``, and
so every run of a biotech company got the generic methodology.

Persisting the answer closes that. But persisting a classification *without its
provenance* creates a worse failure than the one it fixes: the next run cannot tell the
regulator's answer from the platform's own guess, and the first time the SEC is briefly
unreachable a ``T6_model_estimate`` keyword inference overwrites a ``T2_regulator_or_gov``
fact — silently, permanently, and with no way to notice from the row.

So the tier is a column, and the writer refuses to weaken what is already stored.

WHY ``industry_raw`` IS SEPARATE
================================
``industry`` becomes canonical ("Biotechnology") because that is the vocabulary the
playbooks match in. The regulator's own words ("Biological Products, (No Diagnostic
Substances)") go in ``industry_raw`` and stay on screen, because a normalised label a
reader cannot trace back to a source is a claim, and this platform does not make claims
without sources.

ADDITIVE ONLY
=============
Four nullable columns on ``companies``. No constraint, no index, no backfill, no
default — every existing row stays exactly as it is, and NULL correctly means "not yet
classified" rather than "classified as nothing". Downgrade drops only these four.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "039"
down_revision: str | None = "038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("industry_raw", sa.String(200), nullable=True))
    op.add_column("companies", sa.Column("sic_code", sa.String(8), nullable=True))
    op.add_column(
        "companies", sa.Column("classification_tier", sa.String(40), nullable=True)
    )
    op.add_column(
        "companies",
        sa.Column(
            "classification_updated_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("companies", "classification_updated_at")
    op.drop_column("companies", "classification_tier")
    op.drop_column("companies", "sic_code")
    op.drop_column("companies", "industry_raw")
