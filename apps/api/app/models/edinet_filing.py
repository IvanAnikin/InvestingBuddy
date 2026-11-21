"""The EDINET filing index — V3.20 slice 11.2.

EDINET (the Japanese FSA's disclosure system) lists filings BY DAY, never by company:
``/api/v2/documents.json?date=YYYY-MM-DD&type=2`` returns every document submitted that
day. To find one issuer's latest annual or half-year report, the days of the lookback
window are read once and kept here, and an issuer's listing is then a query on its own
EDINET code.

* ``edinet_index_days`` — one row per calendar day read: when, how many filings it held,
  and whether the read is FINAL (a past day) or provisional (today, still filling). A
  day with no row has not been read, and a listing that misses a day says so.
* ``edinet_filings`` — the filing METADATA of each listed document (never its content):
  EDINET's own document id, the filer's EDINET code and securities code, the document
  type, the period, the submission time and the withdrawal status. The content is the
  official PDF, fetched into the corpus by the disclosure acquisition bridge.
"""

from __future__ import annotations

from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EdinetIndexDay(Base):
    __tablename__ = "edinet_index_days"

    day: Mapped[date] = mapped_column(sa.Date, primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    filings: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    #: False while the day may still receive filings (it was read on that same day).
    final: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)


class EdinetFiling(Base):
    __tablename__ = "edinet_filings"
    __table_args__ = (
        sa.Index("ix_edinet_filings_edinet_code_submitted", "edinet_code", "submitted_at"),
        sa.Index("ix_edinet_filings_day", "index_day"),
    )

    #: EDINET's own document id ("S100ZFII").
    doc_id: Mapped[str] = mapped_column(sa.String(16), primary_key=True)
    edinet_code: Mapped[str | None] = mapped_column(sa.String(8))
    #: Five characters: the four-character listing code and a check character.
    sec_code: Mapped[str | None] = mapped_column(sa.String(8))
    filer_name: Mapped[str | None] = mapped_column(sa.String(300))
    doc_type_code: Mapped[str | None] = mapped_column(sa.String(8))
    ordinance_code: Mapped[str | None] = mapped_column(sa.String(8))
    form_code: Mapped[str | None] = mapped_column(sa.String(16))
    doc_description: Mapped[str | None] = mapped_column(sa.String(500))
    period_start: Mapped[date | None] = mapped_column(sa.Date)
    period_end: Mapped[date | None] = mapped_column(sa.Date)
    submitted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    parent_doc_id: Mapped[str | None] = mapped_column(sa.String(16))
    #: "0" filed; "1" withdrawn; "2" a withdrawal notice. Only "0" is ever listed.
    withdrawal_status: Mapped[str | None] = mapped_column(sa.String(4))
    pdf_flag: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    xbrl_flag: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    csv_flag: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    english_flag: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    #: The day listing this filing was read from.
    index_day: Mapped[date] = mapped_column(sa.Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


__all__ = ["EdinetFiling", "EdinetIndexDay"]
