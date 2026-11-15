"""Exhibit 21 subsidiaries and 13D/13G holders → the relationship graph — V3.20 slice 2.3.

Takes a parsed ``SecRelationshipReport`` (``app.integrations.sec_relationships``):

* **Subsidiaries** (10-K Exhibit 21 / 20-F Exhibit 8) → ``parent_of(registrant → child)``
  with the child UNRESOLVED (named, jurisdiction in the note). A row the exhibit lists
  with a stated holding under 50 % is not a subsidiary: it is recorded as
  ``significant_holder_of(registrant → it)`` with the % of capital. Rows under a
  "Joint ventures" / "Affiliates" heading are not written as subsidiaries (counted).
  A later exhibit that no longer lists a child CLOSES its edge — a divestment — and an
  exhibit that could not be read closes nothing.
* **Holders** (structured SCHEDULE 13D / 13G) → ``significant_holder_of(holder → issuer)``
  with ``disclosed_share_pct`` = % of class, ``share_basis="share_class"``, ``as_of`` =
  the event date, ``source_kind`` ``sec_13d`` (active) or ``sec_13g`` (passive). A holder
  resolved when the master holds its CIK, named otherwise. Joint filers report ONE
  holding: the largest reporting person carries it, the others are named in the note. A
  filing at or under 5 % is an exit: it closes the holder's open edge.

No network here; the pipeline step passes the report in.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.legal_entity import EntityRelationship
from app.services.entities.identifiers import SCHEME_CIK
from app.services.entities.master import find_entity_by_identifier
from app.services.entities.relationships import close_relationship, record_relationship
from app.services.entities.vocabulary import (
    REL_PARENT_OF,
    REL_SIGNIFICANT_HOLDER_OF,
    counterparty_key,
)

if TYPE_CHECKING:
    from app.core.config import Settings

SEC_SOURCE = "sec_edgar"
EXIT_THRESHOLD_PCT = 5.0
SUBSIDIARY_MIN_PCT = 50.0
_NON_SUBSIDIARY_SECTIONS = ("joint venture", "affiliate", "equity method", "minority")


@dataclass
class SecRelationshipWriteResult:
    #: ``written`` | ``disabled`` | ``entity_unresolved`` | ``not_sec_registrant`` | ``error``.
    state: str
    subsidiaries_written: int = 0
    minority_holdings_written: int = 0
    subsidiaries_closed: int = 0
    non_subsidiary_rows: int = 0
    subsidiary_source: dict[str, Any] = field(default_factory=dict)
    holders: list[dict[str, Any]] = field(default_factory=list)
    holders_closed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _enabled(cfg: "Settings") -> bool:
    return bool(getattr(cfg, "v3_entity_master_enabled", False))


def _non_subsidiary(section: str | None) -> bool:
    folded = (section or "").casefold()
    return any(w in folded for w in _NON_SUBSIDIARY_SECTIONS)


async def _open_edges(session: Any, **where: Any) -> list[EntityRelationship]:
    stmt = select(EntityRelationship).where(EntityRelationship.effective_to.is_(None))
    for column, value in where.items():
        attr = getattr(EntityRelationship, column)
        stmt = stmt.where(attr.is_(None) if value is None else attr == value)
    return list((await session.execute(stmt)).scalars())


async def record_subsidiaries(
    session: Any, *, entity_id: uuid.UUID, listing: Any, cfg: "Settings",
    result: SecRelationshipWriteResult, today: date,
) -> None:
    as_of = listing.filing_date or today
    excerpt_head = f"{listing.form} {listing.exhibit_type} filed {listing.filing_date}"
    result.subsidiary_source = {
        "form": listing.form, "exhibit": listing.exhibit_type, "accession": listing.accession,
        "filing_date": listing.filing_date.isoformat() if listing.filing_date else None,
        "source_url": listing.source_url, "state": listing.state, "rows": len(listing.rows),
        "truncated": listing.truncated,
    }
    if listing.state != "parsed":
        result.notes.append(f"the {listing.exhibit_type} was read but no subsidiary row was "
                            "recognised; nothing recorded or closed")
        return
    listed_keys: set[str] = set()
    for row in listing.rows:
        if _non_subsidiary(row.section):
            result.non_subsidiary_rows += 1
            continue
        minority = row.ownership_pct is not None and row.ownership_pct < SUBSIDIARY_MIN_PCT
        note = "; ".join(x for x in (
            f"Jurisdiction: {row.jurisdiction}" if row.jurisdiction else None,
            f"Section: {row.section}" if row.section else None,
        ) if x) or None
        await record_relationship(
            session,
            subject_entity_id=entity_id,
            object_entity_id=None,
            counterparty_name=row.name,
            relationship_type=REL_SIGNIFICANT_HOLDER_OF if minority else REL_PARENT_OF,
            source=SEC_SOURCE,
            cfg=cfg,
            source_url=listing.source_url,
            evidence_ref=f"sec:{listing.accession}:{listing.exhibit_type}",
            source_kind="sec_exhibit",
            extraction_method="exhibit_21",
            evidence_excerpt=f"{excerpt_head}: {row.excerpt}",
            as_of=as_of,
            note=note,
            disclosed_share_pct=row.ownership_pct,
            share_basis="capital" if row.ownership_pct is not None else None,
        )
        if minority:
            result.minority_holdings_written += 1
        else:
            result.subsidiaries_written += 1
            key = counterparty_key(row.name)
            if key:
                listed_keys.add(key)
    if listing.truncated:
        # Only part of the list was kept, so absence proves nothing: close nothing.
        return
    for edge in await _open_edges(session, subject_entity_id=entity_id,
                                  object_entity_id=None, relationship_type=REL_PARENT_OF,
                                  extraction_method="exhibit_21"):
        if edge.counterparty_name_key not in listed_keys and (
            edge.as_of is None or edge.as_of < as_of
        ):
            await close_relationship(session, relationship=edge, effective_to=as_of, cfg=cfg)
            result.subsidiaries_closed += 1


async def record_holders(
    session: Any, *, entity_id: uuid.UUID, holders: list[Any], cfg: "Settings",
    result: SecRelationshipWriteResult, today: date,
) -> None:
    for filing in holders:
        lead = filing.lead
        if lead is None:
            result.notes.append(f"{filing.form} {filing.accession} states no percentage")
            continue
        holder = None
        holder_cik = lead.cik or filing.filer_cik
        if holder_cik:
            try:
                holder = await find_entity_by_identifier(
                    session, scheme=SCHEME_CIK, value=holder_cik, cfg=cfg)
            except ValueError:
                holder = None
        if holder is not None and holder.id == entity_id:
            result.notes.append(f"{filing.accession}: the issuer reporting on itself; skipped")
            continue
        pct = float(lead.percent_of_class or 0.0)
        as_of = filing.event_date or filing.filing_date or today
        others = [p.name for p in filing.persons if p is not lead]
        kind = "sec_13d" if filing.schedule == "13D" else "sec_13g"
        if pct <= EXIT_THRESHOLD_PCT:
            for edge in await _open_edges(
                session, object_entity_id=entity_id,
                relationship_type=REL_SIGNIFICANT_HOLDER_OF,
                **({"subject_entity_id": holder.id} if holder is not None else
                   {"subject_entity_id": None,
                    "counterparty_name_key": counterparty_key(lead.name)}),
            ):
                if edge.as_of is None or edge.as_of <= as_of:
                    await close_relationship(session, relationship=edge, effective_to=as_of,
                                             cfg=cfg)
                    result.holders_closed.append(lead.name)
            result.holders.append({"holder": lead.name, "percent_of_class": pct,
                                   "schedule": filing.schedule, "as_of": as_of.isoformat(),
                                   "state": "at_or_below_5pct", "source_url": filing.source_url})
            continue
        excerpt = (
            f"{filing.form} filed {filing.filing_date}: {lead.name} reports "
            f"{pct:g}% of {filing.class_title or 'the class'} as of {as_of.isoformat()}"
            + (f" (filed jointly with {', '.join(others[:5])})" if others else "")
        )
        note = "; ".join(x for x in (
            "active holder (Schedule 13D)" if filing.schedule == "13D"
            else "passive holder (Schedule 13G)",
            f"reporting person type {lead.person_type}" if lead.person_type else None,
        ) if x)
        recorded = await record_relationship(
            session,
            subject_entity_id=holder.id if holder is not None else None,
            object_entity_id=entity_id,
            counterparty_name=None if holder is not None else lead.name,
            relationship_type=REL_SIGNIFICANT_HOLDER_OF,
            source=SEC_SOURCE,
            cfg=cfg,
            source_url=filing.source_url,
            evidence_ref=f"sec:{filing.accession}",
            source_kind=kind,
            extraction_method="filing_xml",
            evidence_excerpt=excerpt,
            as_of=as_of,
            note=note,
            disclosed_share_pct=pct,
            share_basis="share_class",
        )
        result.holders.append({
            "holder": lead.name, "holder_resolved": holder is not None,
            "percent_of_class": pct, "schedule": filing.schedule, "as_of": as_of.isoformat(),
            "state": "recorded",
            "relationship_id": str(recorded.id) if recorded is not None else None,
            "source_url": filing.source_url,
        })


async def record_sec_relationships(
    session: Any, *, entity_id: uuid.UUID, report: Any, cfg: "Settings",
    today: date | None = None,
) -> SecRelationshipWriteResult:
    """Record one ``SecRelationshipReport``. Flush-only; the caller commits."""
    result = SecRelationshipWriteResult(state="written")
    if not _enabled(cfg):
        result.state = "disabled"
        return result
    day = today or datetime.now(timezone.utc).date()
    result.errors.extend(getattr(report, "errors", None) or [])
    result.notes.extend(getattr(report, "notes", None) or [])
    if getattr(report, "subsidiaries", None) is not None:
        await record_subsidiaries(session, entity_id=entity_id, listing=report.subsidiaries,
                                  cfg=cfg, result=result, today=day)
    await record_holders(session, entity_id=entity_id,
                         holders=list(getattr(report, "holders", None) or []),
                         cfg=cfg, result=result, today=day)
    if result.errors and not (result.subsidiary_source or result.holders):
        result.state = "error"
    await session.flush()
    return result


__all__ = [
    "EXIT_THRESHOLD_PCT",
    "SEC_SOURCE",
    "SUBSIDIARY_MIN_PCT",
    "SecRelationshipWriteResult",
    "record_sec_relationships",
]
