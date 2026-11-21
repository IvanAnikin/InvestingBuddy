"""OpenDART's corp-code list, held — V3.20 slice 11.3.

OpenDART identifies a filer by an eight-digit ``corp_code``, never by its stock code,
and the only map between the two is ``corpCode.xml``: a 3.6 MB zip of every filer.
Measured from this project on 2026-11-17, OpenDART served it at about 7 KB/s (499 s).
A research run cannot wait for that, so the LISTED rows are held here, refreshed by an
operator job (``scripts/opendart_corp_codes_refresh.py``) and read by stock code.

Each row is one listed company as OpenDART states it: its corp code, its six-character
KRX code, its Korean and English names, and when the list was read. An unlisted filer
is never held.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OpendartCorpCode(Base):
    __tablename__ = "opendart_corp_codes"
    __table_args__ = (sa.Index("ix_opendart_corp_codes_stock_code", "stock_code"),)

    corp_code: Mapped[str] = mapped_column(sa.String(8), primary_key=True)
    stock_code: Mapped[str] = mapped_column(sa.String(6), nullable=False)
    name_ko: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    name_en: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)
    #: When the list this row came from was read.
    refreshed_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
