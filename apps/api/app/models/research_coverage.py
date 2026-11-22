"""Independent / sponsored research coverage index — V3.21 slice 21.7 (migration 064).

One row per research-provider page URL: an Edison Group ``/equity/<company>/`` page or
``/research/<note>/`` note read from its public sitemap, or a Redeye / Research Tree /
mwb research page found by a host-restricted web search. Only the URL, the provider's
slug, a normalised name key, a title and the dates are held — never a page body, never a
claim. See ``services/research_coverage`` and ADR-114.

Rows are never deleted by the refresh: a page that leaves the sitemap keeps its
``last_seen_at``, so "listed in the latest sitemap" is ``last_seen_at`` equal to the
provider's newest refresh stamp.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PAGE_KINDS: tuple[str, ...] = ("equity", "note")
DISCOVERED_VIA: tuple[str, ...] = ("sitemap", "search")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ResearchCoveragePage(Base):
    __tablename__ = "research_coverage_pages"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    #: ``edison`` | ``redeye`` | ``research_tree`` | ``mwb_research``.
    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    url: Mapped[str] = mapped_column(sa.Text, nullable=False)
    page_kind: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    #: The provider's own path slug (``ceres-power-holdings``); NULL when the URL has none.
    company_slug: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    #: ``research_coverage.names.name_key`` of the slug (equity) or of the searched
    #: company's name (search hit). NULL for a note: a note's slug is its headline.
    name_key: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    #: Derived from the slug (sitemap) or the search result title (search). Untrusted
    #: display text, clipped; never evidence.
    title: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    #: The sitemap's ``<lastmod>``, or the search provider's published hint.
    lastmod: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True),
                                                     nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True),
                                                    nullable=False, default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True),
                                                   nullable=False, default=_now)
    discovered_via: Mapped[str] = mapped_column(sa.String(16), nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("url", name="uq_research_coverage_pages_url"),
        sa.CheckConstraint("page_kind IN ('equity', 'note')",
                           name="ck_research_coverage_pages_page_kind"),
        sa.CheckConstraint("discovered_via IN ('sitemap', 'search')",
                           name="ck_research_coverage_pages_discovered_via"),
        sa.Index("ix_research_coverage_pages_name_key", "name_key"),
        sa.Index("ix_research_coverage_pages_provider_last_seen", "provider",
                 "last_seen_at"),
    )


__all__ = ["DISCOVERED_VIA", "PAGE_KINDS", "ResearchCoveragePage"]
