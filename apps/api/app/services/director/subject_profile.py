"""What the company's own documents say it produces — V3.18.4.

The planner instantiates per-commodity questions for the materials a company actually
sells. That list comes from here, and here reads only the company's OWN corpus (its
filings and issuer documents), never a table keyed by ticker: a playbook that already
knew what each company produced would be an answer key, not a methodology.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.macro.commodities import CommodityMention, identify_commodities

#: How much of the corpus is read. Enough to see what a 10-K is about; bounded so a
#: company with a large corpus cannot make planning slow.
MAX_CHUNKS = 600


@dataclass
class SubjectProfile:
    commodities: list[CommodityMention] = field(default_factory=list)
    chunks_read: int = 0
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "commodities": [c.to_dict() for c in self.commodities],
            "chunks_read": self.chunks_read,
            "note": self.note,
        }


async def build_subject_profile(session: Any, company: Any) -> SubjectProfile:
    """Never raises; an unreadable corpus is an empty profile with the reason."""
    from sqlalchemy import select

    from app.models.research_chunk import ResearchDocumentChunk

    profile = SubjectProfile()
    try:
        rows = (
            await session.execute(
                select(ResearchDocumentChunk.text)
                .where(ResearchDocumentChunk.company_id == company.id)
                .limit(MAX_CHUNKS)
            )
        ).scalars().all()
    except Exception as exc:  # noqa: BLE001 - planning must not fail on a profile
        profile.note = f"corpus unreadable ({type(exc).__name__})"
        return profile
    profile.chunks_read = len(rows)
    texts = list(rows)
    # The registrant's own name is evidence too ("… Copper Corp"), weighted like one
    # mention per chunk read at most — enough to break a tie, never to decide alone.
    name = str(getattr(company, "name", "") or "")
    if name:
        texts.append(" ".join([name] * max(1, min(5, len(rows) // 20 or 1))))
    profile.commodities = identify_commodities(texts)
    if not profile.commodities:
        profile.note = (
            "no commodity is named often enough in the company's own documents to "
            "research one specifically"
            if rows
            else "the company has no corpus yet; commodities were not identified"
        )
    return profile


__all__ = ["MAX_CHUNKS", "SubjectProfile", "build_subject_profile"]
