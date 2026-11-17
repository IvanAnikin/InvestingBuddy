"""TWSE material announcements (重大訊息), held — V3.20 slice 11.4.

TWSE's open-data feed ``t187ap04_L`` (上市公司每日重大訊息) states the material
announcements of every listed company for ONE announcement day only: the next day's
read replaces it, and the open data offers no history. A research run that read the feed
live would almost always find nothing for its issuer, so every row read is HELD here —
by research runs and by an operator job (``scripts/twse_material_refresh.py``) — and a
listing reads the held rows. History begins on the first read; nothing earlier exists.

Each row is one announcement exactly as the feed states it: the company code and short
name, the announcement date and time (Asia/Taipei, stored in UTC), the clause of TWSE's
material-information rules it was filed under, the fact date, the subject and the body.
``ref`` is derived from those fields (code, date, time and a content digest), so a
re-read of the same announcement is recognised and never stored twice.
"""

from __future__ import annotations

from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TwseMaterialAnnouncement(Base):
    __tablename__ = "twse_material_announcements"
    __table_args__ = (
        sa.Index("ix_twse_material_announcements_code_announced", "company_code",
                 "announced_at"),
    )

    #: ``<code>-<yyyymmdd>-<hhmmss>-<digest10>`` — the document ref the corpus uses.
    ref: Mapped[str] = mapped_column(sa.String(40), primary_key=True)
    company_code: Mapped[str] = mapped_column(sa.String(6), nullable=False)
    #: The feed's 公司名稱 — TWSE's short Chinese name for the company.
    company_name: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    announced_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    #: 符合條款, verbatim ("第51款").
    clause: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="")
    #: 事實發生日, when stated.
    fact_date: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    subject: Mapped[str] = mapped_column(sa.Text, nullable=False)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    #: SHA-256 of the fields ``ref`` is derived from.
    content_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: When this row was first read from the feed.
    fetched_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
