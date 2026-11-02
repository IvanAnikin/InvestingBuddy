"""Which listing the SEC knows this company by — ADR-075.

WHY
===
QA campaign 2026-11, live Cellectis report ``1df9100c``. The research job ran on the
company row ``ALCLS`` / Euronext Paris. Cellectis S.A. is also an SEC registrant (CIK
1627281; ADS ``CLLS`` on Nasdaq; files 20-F and 6-K). Every SEC step asked
``is_sec_eligible(company.exchange)``, got False for Paris, and stopped: the core filings
were skipped as "not an SEC registrant", classification failed, no 20-F was read, and
the chair said "no FY2024 results available".

ADR-061 already says an issuer is not a security. This module applies that to the SEC
question: the issuer files with the SEC when ANY verified line of the same issuer is
SEC-eligible, not only the line the job happened to start from.

THE ORDER, AND WHAT EACH STEP NEEDS
===================================
1. **The company's own line**, when it is SEC-eligible. Behaviour is unchanged: the same
   ticker and exchange every caller used before, no lookup.
2. **A listing of the same legal entity** (``companies.legal_entity_id`` →
   ``securities`` → ``security_listings``): open window, not delisted or suspended, on an
   SEC-eligible venue. It counts only when it is verified: the entity holds an open CIK
   identifier (accepted under the claims rules), or the SEC's own ticker list carries that
   ticker under a name that agrees with the entity's name.
3. **The SEC company-tickers directory**, only when 1 and 2 give nothing. ALL must hold:
   - the company has a real name (not its ticker) and a known non-US country (the row's
     ``country``, else its venue's country);
   - exactly ONE registrant (one CIK) in the directory carries the SAME normalised name
     (ADR-061's name-only rule: exact after legal suffixes are dropped and exchange
     abbreviations expanded, never a prefix);
   - that registrant's own SEC record states a domicile equal to the company's country:
     its country of incorporation, else its business address. EDGAR's incorporation code
     is not always right (Shell plc reads ``DC``), so ``sec_registrant_domicile`` counts a
     US state code only when the business address is not foreign (ADR-061 §4).

A namesake is never read as the issuer. Two registrants with one normalised name (Orion
Oyj vs ``Orion S.A.`` and ``Orion Group Holdings``; UBS Group AG vs its subsidiary UBS AG)
are ambiguous and give nothing. A US-domiciled namesake of a French company fails the
domicile check. An unreadable directory or registrant record gives nothing, never a
guess.

No new search provider, no name-only link, no alias table. Never raises.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: The company's own listing is SEC-eligible.
BASIS_OWN = "own_listing"
#: A verified listing of the same legal entity (entity master, ADR-061).
BASIS_LEGAL_ENTITY = "same_legal_entity"
#: The SEC directory's exact name + the registrant's own domicile record.
BASIS_SEC_DIRECTORY = "sec_directory_name_and_domicile"

_BASIS_TEXT = {
    BASIS_OWN: "the company's own listing",
    BASIS_LEGAL_ENTITY: "same legal entity",
    BASIS_SEC_DIRECTORY: "same issuer: exact name in the SEC company tickers list and "
                         "the registrant's domicile",
}

_INACTIVE_LISTING_STATUSES = frozenset({"delisted", "suspended"})


@dataclass(frozen=True)
class _Subject:
    """A plain snapshot of the company row (never the ORM instance itself)."""

    ticker: str
    exchange: str | None
    name: str
    country: str | None
    legal_entity_id: Any


@dataclass(frozen=True)
class SecListing:
    """The ticker, exchange and (when known) CIK the SEC knows this company by."""

    ticker: str
    exchange: str | None
    cik: str | None
    basis: str
    detail: str = ""

    @property
    def is_sibling(self) -> bool:
        """True when this is NOT the company's own line."""
        return self.basis != BASIS_OWN

    def describe(self) -> str:
        venue = f"{self.ticker}·{self.exchange}" if self.exchange else self.ticker
        return f"SEC filings via {venue} ({_BASIS_TEXT.get(self.basis, self.basis)})"

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["description"] = self.describe()
        return out


async def sec_listing_for(
    session: Any, company: Any, *, cfg: Any = None
) -> SecListing | None:
    """The SEC basis for ``company``, or None when the issuer is not an SEC registrant
    on any verified evidence. Never raises."""
    from app.services.exchange_registry import is_sec_eligible

    ticker = str(getattr(company, "ticker", "") or "").strip().upper()
    exchange = getattr(company, "exchange", None)
    if ticker and is_sec_eligible(exchange):
        return SecListing(ticker=ticker, exchange=exchange, cik=None, basis=BASIS_OWN)
    # Every attribute is read BEFORE any savepoint: rolling one back expires the ORM
    # instance, and a later lazy read would raise MissingGreenlet instead of the error.
    subject = _Subject(
        ticker=ticker,
        exchange=exchange,
        name=str(getattr(company, "name", "") or "").strip(),
        country=str(getattr(company, "country", "") or "").strip() or None,
        legal_entity_id=getattr(company, "legal_entity_id", None),
    )
    try:
        found = await _legal_entity_listing(session, subject, cfg=cfg)
        if found is None:
            found = await _directory_listing(subject, cfg=cfg)
    except Exception as exc:  # noqa: BLE001 - no basis is an honest answer; a crash is not
        logger.warning("SEC listing resolution failed (%s)", type(exc).__name__)
        return None
    return found


# ── 2. The same legal entity ─────────────────────────────────────────────── #


def _entity_master_enabled(cfg: Any) -> bool:
    if cfg is None:
        from app.core.config import settings as default_settings

        cfg = default_settings
    return bool(getattr(cfg, "v3_entity_master_enabled", False))


async def _legal_entity_listing(
    session: Any, subject: _Subject, *, cfg: Any
) -> SecListing | None:
    entity_id = subject.legal_entity_id
    if session is None or not isinstance(entity_id, uuid.UUID):
        return None
    if not _entity_master_enabled(cfg):
        return None
    from sqlalchemy import select

    from app.models.legal_entity import (
        EntityIdentifier,
        LegalEntity,
        Security,
        SecurityListing,
    )
    from app.services.entities.claims import _listing_is_sec_eligible
    from app.services.entities.identifiers import SCHEME_CIK

    async with session.begin_nested():
        rows = (
            await session.execute(
                select(
                    SecurityListing.ticker,
                    SecurityListing.exchange_code,
                    SecurityListing.listing_status,
                    SecurityListing.is_primary,
                )
                .join(Security, Security.id == SecurityListing.security_id)
                .where(
                    Security.legal_entity_id == entity_id,
                    SecurityListing.effective_to.is_(None),
                )
            )
        ).all()
        ciks = (
            await session.execute(
                select(EntityIdentifier.value_normalized).where(
                    EntityIdentifier.legal_entity_id == entity_id,
                    EntityIdentifier.scheme == SCHEME_CIK,
                    EntityIdentifier.effective_to.is_(None),
                )
            )
        ).scalars().all()
        legal_name = (
            await session.execute(
                select(LegalEntity.legal_name).where(LegalEntity.id == entity_id)
            )
        ).scalar_one_or_none()

    candidates = sorted(
        (
            (str(t).upper(), str(code))
            for t, code, status, _primary in rows
            if t
            and _listing_is_sec_eligible(code)
            and str(status or "").lower() not in _INACTIVE_LISTING_STATUSES
        ),
        key=lambda item: item,
    )
    if not candidates:
        return None
    entity_cik = _cik(ciks[0]) if len(set(ciks)) == 1 else None
    directory = await _load_sec_directory(cfg)
    names = [n for n in (legal_name, subject.name) if n]
    for ticker, code in candidates:
        rows_for_ticker = [r for r in directory if r.ticker == ticker]
        if entity_cik is not None:
            # The CIK is the entity's own, accepted under the claims rules. A directory
            # that lists this ticker under ANOTHER CIK contradicts it: refuse.
            if any(_cik(r.cik) not in (None, entity_cik) for r in rows_for_ticker):
                continue
            return SecListing(
                ticker=ticker, exchange=code, cik=entity_cik, basis=BASIS_LEGAL_ENTITY,
                detail=f"listing of legal entity {entity_id}; CIK held by the entity",
            )
        # No CIK on the entity: the SEC's own list must carry this ticker under a name
        # that agrees with the entity's (the ticker-anchored rule, as discovery uses).
        for row in rows_for_ticker:
            if row.cik and any(_ticker_names_agree(n, row.name) for n in names):
                return SecListing(
                    ticker=ticker, exchange=code, cik=_cik(row.cik),
                    basis=BASIS_LEGAL_ENTITY,
                    detail=(f"listing of legal entity {entity_id}; the SEC lists "
                            f"{ticker} as {row.name!r}"),
                )
    return None


# ── 3. The SEC directory, under the domicile check ───────────────────────── #


async def _directory_listing(subject: _Subject, *, cfg: Any) -> SecListing | None:
    from app.services.discovery.directories import _expand
    from app.services.discovery.identity import normalised_name
    from app.services.discovery_signal_extractor import is_placeholder_company_name
    from app.services.exchange_registry import country_for_exchange, is_sec_eligible

    name, exchange = subject.name, subject.exchange
    if not name or is_placeholder_company_name(name, subject.ticker):
        return None
    country = subject.country or (country_for_exchange(exchange) if exchange else None)
    if not country or country == "United States":
        return None
    wanted = _expand(normalised_name(name))
    if not wanted:
        return None
    directory = await _load_sec_directory(cfg)
    hits = [
        r for r in directory
        if r.cik and _expand(normalised_name(r.name)) == wanted
    ]
    registrants = {c for c in (_cik(r.cik) for r in hits) if c is not None}
    if len(registrants) != 1:
        # None: not an SEC registrant on this evidence. Several: namesakes (or a parent
        # and its registered subsidiary) — which one is the issuer is not decidable here.
        return None
    cik = next(iter(registrants))
    record = await _registrant_domicile(cik, cfg)
    if record is None:
        return None
    stated = record.incorporation or record.business
    basis_field = "incorporation" if record.incorporation else "business address"
    if stated != country:
        return None
    row = sorted(hits, key=lambda r: r.ticker)[0]
    venue = str(row.mic or "").upper()
    sec_exchange = venue if venue and is_sec_eligible(venue) else "US"
    return SecListing(
        ticker=row.ticker, exchange=sec_exchange, cik=cik, basis=BASIS_SEC_DIRECTORY,
        detail=(f"the SEC lists {row.ticker} as {row.name!r} (CIK {cik}), the only "
                f"registrant with this name; its record states {basis_field} {stated}, "
                f"the company's country"),
    )


# ── Network leaves (patched to inert in the unit suite) ──────────────────── #


async def _load_sec_directory(cfg: Any = None) -> list[Any]:
    """The SEC company-tickers-and-exchanges list, cached per process per day."""
    from app.services.discovery import directories

    try:
        return list(await directories.load(directories.SEC, cfg=cfg))
    except Exception:  # noqa: BLE001 - an unreadable directory is a missing source
        return []


async def _registrant_domicile(cik: str, cfg: Any = None) -> Any:
    """The registrant's own domicile record (``RegistrantDomicile``) or None."""
    from app.services.discovery.directories import sec_registrant_domicile

    return await sec_registrant_domicile(cik, cfg=cfg)


# ── Helpers ──────────────────────────────────────────────────────────────── #


def _cik(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text.isdigit():
        return None
    return str(int(text))


def _ticker_names_agree(ours: str, theirs: str) -> bool:
    from app.services.discovery.directories import _names_agree
    from app.services.discovery.identity import normalised_name

    return _names_agree(normalised_name(ours), normalised_name(theirs), ticker_matched=True)


__all__ = [
    "BASIS_LEGAL_ENTITY",
    "BASIS_OWN",
    "BASIS_SEC_DIRECTORY",
    "SecListing",
    "sec_listing_for",
]
