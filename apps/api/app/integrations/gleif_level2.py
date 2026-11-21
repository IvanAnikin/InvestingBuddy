"""GLEIF Level 2 — "who owns whom" — V3.20 slice 2.2 (spec §3.2).

GLEIF's Level 2 data is the accounting-consolidation parent of every LEI holder, filed
by the entity itself and corroborated (or not) by its LEI issuer. CC0, free, no key.

THREE ANSWERS, NEVER TWO
========================
For each of the direct and the ultimate parent, GLEIF answers in one of three ways:

* ``200`` on ``/lei-records/{lei}/{kind}-parent``  → a parent LEI record. With the
  ``/{kind}-parent-relationship`` record beside it: its status, its start date and how
  well it was corroborated.
* ``404`` there, ``200`` on ``/{kind}-parent-reporting-exception`` → NO parent reported,
  with a REASON (``NATURAL_PERSONS``, ``NON_CONSOLIDATING``, ``NO_KNOWN_PERSON``, …).
* ``404`` on both                                 → nothing is known. Not "independent".

Collapsing the last two is how "controlled by private individuals" would become
"independent company". This module keeps the three apart, and so does the writer
(``app.services.entities.gleif_parents``).

NETWORK HERE, SEMANTICS THERE
=============================
A test refuses a network import inside ``app.services.entities``, so the client lives
here and the writer receives a parsed ``ParentReport``. The fetcher is injected: the unit
suite drives this module with canned JSON and no socket (``fetch=``), and the conftest
keeps the company-research step offline by default.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any

GLEIF_BASE_URL = "https://api.gleif.org/api/v1"
GLEIF_SOURCE_ID = "gleif"
KINDS: tuple[str, ...] = ("direct", "ultimate")

#: How well the LEI issuer checked the reported relationship → edge confidence. An
#: entity-supplied-only link is still the entity's own regulatory filing, so it is kept,
#: at a lower confidence, rather than dropped.
CORROBORATION_CONFIDENCE: dict[str, float] = {
    "FULLY_CORROBORATED": 1.0,
    "PARTIALLY_CORROBORATED": 0.9,
    "ENTITY_SUPPLIED_ONLY": 0.8,
}
DEFAULT_CONFIDENCE = 0.8

#: ``(status, json or None)``. A transport failure raises; the caller contains it.
Fetch = Callable[[str], Awaitable[tuple[int, Any]]]


@dataclass(frozen=True)
class GleifEntity:
    lei: str
    legal_name: str
    country: str | None = None
    jurisdiction: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class ParentLink:
    """A reported parent. ``kind`` is ``direct`` | ``ultimate``."""

    kind: str
    parent: GleifEntity
    source_url: str
    relationship_url: str | None = None
    relationship_status: str | None = None
    relationship_type: str | None = None
    start_date: date | None = None
    corroboration: str | None = None

    @property
    def confidence(self) -> float:
        return CORROBORATION_CONFIDENCE.get(self.corroboration or "", DEFAULT_CONFIDENCE)

    @property
    def active(self) -> bool:
        # A relationship record we could not read is not evidence it ended.
        return (self.relationship_status or "ACTIVE").upper() == "ACTIVE"


@dataclass(frozen=True)
class ParentException:
    """GLEIF's reason for NOT reporting a parent."""

    kind: str
    reason: str
    category: str | None
    source_url: str


@dataclass
class ParentReport:
    """Everything one LEI's Level-2 lookups returned, and what they could not."""

    lei: str
    links: list[ParentLink] = field(default_factory=list)
    exceptions: list[ParentException] = field(default_factory=list)
    #: Kinds for which GLEIF has neither a parent nor an exception: unknown.
    not_reported: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["retrieved_at"] = self.retrieved_at.isoformat()
        for link in out["links"]:
            if link.get("start_date"):
                link["start_date"] = link["start_date"].isoformat()
        return out


# ── URLs ────────────────────────────────────────────────────────────────── #


def record_url(lei: str) -> str:
    return f"{GLEIF_BASE_URL}/lei-records/{lei.upper()}"


def parent_url(lei: str, kind: str) -> str:
    return f"{record_url(lei)}/{kind}-parent"


def relationship_url(lei: str, kind: str) -> str:
    return f"{record_url(lei)}/{kind}-parent-relationship"


def exception_url(lei: str, kind: str) -> str:
    return f"{record_url(lei)}/{kind}-parent-reporting-exception"


# ── Pure parsers ────────────────────────────────────────────────────────── #


def _data(payload: Any) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        data = data[0] if data else None
    return data if isinstance(data, dict) else {}


def parse_entity(payload: Any) -> GleifEntity | None:
    """A ``lei-records`` document → ``GleifEntity``; None when it carries no LEI."""
    data = _data(payload)
    attrs = data.get("attributes") or {}
    lei = str(attrs.get("lei") or data.get("id") or "").strip().upper()
    entity = attrs.get("entity") or {}
    legal = entity.get("legalName") or {}
    name = str((legal.get("name") if isinstance(legal, dict) else legal) or "").strip()
    if not lei or not name:
        return None
    address = entity.get("legalAddress") or {}
    country = str(address.get("country") or "").strip().upper()[:2] or None
    juris = str(entity.get("jurisdiction") or "").strip().upper() or None
    return GleifEntity(
        lei=lei, legal_name=name, country=country, jurisdiction=juris,
        status=entity.get("status"),
    )


def _iso_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def parse_relationship(payload: Any) -> dict[str, Any]:
    """A ``relationship-records`` document → status / type / start date / corroboration."""
    attrs = _data(payload).get("attributes") or {}
    rel = attrs.get("relationship") or {}
    start = None
    for period in rel.get("periods") or []:
        if (period or {}).get("type") == "RELATIONSHIP_PERIOD":
            start = _iso_date(period.get("startDate"))
            break
    registration = attrs.get("registration") or {}
    return {
        "status": rel.get("status"),
        "type": rel.get("type"),
        "start_date": start,
        "corroboration": registration.get("corroborationLevel"),
    }


def parse_exception(payload: Any) -> tuple[str | None, str | None]:
    """A ``reporting-exceptions`` document → ``(reason, category)``."""
    attrs = _data(payload).get("attributes") or {}
    reason = str(attrs.get("reason") or "").strip().upper() or None
    category = str(attrs.get("category") or "").strip().upper() or None
    return reason, category


# ── The lookup ──────────────────────────────────────────────────────────── #


async def _default_fetch(url: str) -> tuple[int, Any]:  # pragma: no cover - network
    import httpx

    from app.core.outbound_identity import USER_AGENT_HEADER, research_user_agent

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            url,
            headers={
                USER_AGENT_HEADER: research_user_agent(),
                "Accept": "application/vnd.api+json",
            },
        )
    if resp.status_code != 200:
        return resp.status_code, None
    return 200, resp.json()


async def fetch_parent_report(lei: str, *, fetch: Fetch | None = None) -> ParentReport:
    """Ask GLEIF for ``lei``'s direct and ultimate parent. Never raises."""
    get = fetch or _default_fetch
    lei = (lei or "").strip().upper()
    report = ParentReport(lei=lei)
    if not lei:
        report.errors.append("no LEI to look up")
        return report
    for kind in KINDS:
        try:
            await _one_kind(report, lei, kind, get)
        except Exception as exc:  # noqa: BLE001 - one kind failing keeps the other
            report.errors.append(f"{kind} parent lookup failed ({type(exc).__name__})")
    return report


async def _one_kind(report: ParentReport, lei: str, kind: str, get: Fetch) -> None:
    url = parent_url(lei, kind)
    status, payload = await get(url)
    if status == 200:
        parent = parse_entity(payload)
        if parent is None:
            report.errors.append(f"{kind} parent record carried no LEI or name")
            return
        if parent.lei == lei:
            # GLEIF never does this; a self-parent would be a check-constraint error.
            report.errors.append(f"{kind} parent is the entity itself")
            return
        rel: dict[str, Any] = {}
        rel_url = relationship_url(lei, kind)
        try:
            rel_status, rel_payload = await get(rel_url)
            if rel_status == 200:
                rel = parse_relationship(rel_payload)
        except Exception as exc:  # noqa: BLE001 - the parent stands without the period
            report.errors.append(
                f"{kind} parent relationship record unread ({type(exc).__name__})"
            )
        report.links.append(ParentLink(
            kind=kind, parent=parent, source_url=url,
            relationship_url=rel_url if rel else None,
            relationship_status=rel.get("status"),
            relationship_type=rel.get("type"),
            start_date=rel.get("start_date"),
            corroboration=rel.get("corroboration"),
        ))
        return
    if status != 404:
        report.errors.append(f"{kind} parent lookup returned HTTP {status}")
        return
    exc_url = exception_url(lei, kind)
    exc_status, exc_payload = await get(exc_url)
    if exc_status == 200:
        reason, category = parse_exception(exc_payload)
        if reason:
            report.exceptions.append(ParentException(
                kind=kind, reason=reason, category=category, source_url=exc_url,
            ))
            return
        report.errors.append(f"{kind} parent reporting exception carried no reason")
        return
    if exc_status == 404:
        report.not_reported.append(kind)
        return
    report.errors.append(f"{kind} parent reporting exception returned HTTP {exc_status}")


__all__ = [
    "CORROBORATION_CONFIDENCE",
    "GLEIF_BASE_URL",
    "GLEIF_SOURCE_ID",
    "KINDS",
    "Fetch",
    "GleifEntity",
    "ParentException",
    "ParentLink",
    "ParentReport",
    "exception_url",
    "fetch_parent_report",
    "parent_url",
    "parse_entity",
    "parse_exception",
    "parse_relationship",
    "record_url",
    "relationship_url",
]
