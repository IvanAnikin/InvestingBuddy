"""Identifier claims and their verification gate — V3.2 Slice 2.3.

THE PROMOTION PATH, APPLIED TO IDENTITY
=======================================
``DATA_AND_EVIDENCE_ARCHITECTURE.md`` §4.1 states the rule for everything a
provider says: it enters as a claim, and it must survive independent verification
before it becomes canonical. An identifier is no different, and it is arguably the
worst place to make an exception — a wrong LEI does not produce a wrong sentence,
it silently attaches one issuer's entire filing history to another.

So a source produces an ``IdentifierClaim``. ``verify_identifier_claim`` either
records it or **refuses it with a reason**, and the refusal is returned rather than
dropped: a rejected claim is exactly the "store rejected companies and failed
analyses, they are valuable learning data" rule (CLAUDE.md rule 8) applied to
identity, and it is what makes a source's accuracy measurable later.

THE RULE THAT IS THE BOEING BUG
===============================
``venue_not_sec_eligible``. SEC's ``company_tickers.json`` indexes **US registrants
by ticker string alone**, so looking a non-US local ticker up in it does not fail —
it returns an unrelated US issuer. ``BA`` + LSE returned Boeing's CIK for BAE
Systems; ``MC`` + PA returned Moelis for LVMH. V2 fixed that with an
``exchange_registry.sec_eligible`` flag consulted at one call site. Here it is a
**verification rule**: a CIK claimed for a listing on a venue SEC does not cover is
refused, whatever produced it.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
=========================================
No network. ``IdentifierSource`` is a Protocol and ``StaticIdentifierSource`` is
its reference implementation; live GLEIF and SEC adapters are slice 2.3.1, where
rate limits, failure modes and opt-in test gating belong. The 2.1 test that parses
every module in this package and refuses a network import covers this file and will
fail if one appears.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from sqlalchemy import select

from app.models.legal_entity import (
    EntityIdentifier,
    LegalEntity,
    Security,
    SecurityListing,
)
from app.services.entities.identifiers import (
    SCHEME_CIK,
    SUBJECT_LEGAL_ENTITY,
    SUBJECT_SECURITY,
    normalize_identifier,
)
from app.services.entities.master import record_identifier
from app.services.exchange_registry import is_sec_eligible

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings

# ── Rejection reasons ────────────────────────────────────────────────────── #

#: It fails its own checksum or shape. Refused, never stored at low confidence.
REJECTED_INVALID_IDENTIFIER = "invalid_identifier"
#: Currently held by a different subject. Which of the two is right is an evidence
#: question, and answering it by overwriting is the merge the schema prevents.
REJECTED_HELD_BY_ANOTHER_ENTITY = "held_by_another_entity"
#: A CIK claimed for a listing on a venue SEC's ticker index does not cover.
REJECTED_VENUE_NOT_SEC_ELIGIBLE = "venue_not_sec_eligible"
#: An ISIN claimed for a legal entity, or a LEI for a security.
REJECTED_SUBJECT_MISMATCH = "subject_mismatch"
#: The claim names no source. An unsourced identifier is a guess.
REJECTED_NO_SOURCE = "no_source"
#: The subject the claim names does not exist.
REJECTED_SUBJECT_NOT_FOUND = "subject_not_found"
#: The entity master is off.
REJECTED_DISABLED = "disabled"

REJECTION_REASONS: frozenset[str] = frozenset(
    {
        REJECTED_INVALID_IDENTIFIER,
        REJECTED_HELD_BY_ANOTHER_ENTITY,
        REJECTED_VENUE_NOT_SEC_ELIGIBLE,
        REJECTED_SUBJECT_MISMATCH,
        REJECTED_NO_SOURCE,
        REJECTED_SUBJECT_NOT_FOUND,
        REJECTED_DISABLED,
    }
)


@dataclass(frozen=True)
class IdentifierClaim:
    """What a source says an entity's or a security's identifier is.

    Not evidence. A claim is attributed, checked and either promoted or refused —
    see the module docstring.
    """

    scheme: str
    value: str
    source: str
    source_url: str | None = None
    jurisdiction: str | None = None
    #: The source's own confidence, 0.0-1.0. Carried through so a registry's
    #: certainty and an aggregator's guess do not read alike after persistence.
    confidence: float = 1.0
    effective_from: date | None = None
    #: Free-form provenance for auditing — the query that produced it, a record id.
    detail: str | None = None


@dataclass
class ClaimOutcome:
    """The result of putting one claim through the gate."""

    claim: IdentifierClaim
    accepted: bool
    #: One of ``REJECTION_REASONS`` when ``accepted`` is False, else ``None``.
    rejection_reason: str | None = None
    #: Human-readable detail. Never the only record of why — the reason code is.
    detail: str | None = None
    identifier_id: uuid.UUID | None = None

    @property
    def rejected(self) -> bool:
        return not self.accepted


@dataclass
class IdentifierQuery:
    """What a source is being asked to look up."""

    legal_name: str | None = None
    ticker: str | None = None
    exchange: str | None = None
    jurisdiction: str | None = None
    known_identifiers: dict[str, str] = field(default_factory=dict)


# ── Withheld findings (V3.2 Slice 2.3.1) ─────────────────────────────────── #

#: Several records matched the name and nothing separates them. The single most
#: important one: GLEIF's name filter is a PARTIAL match, so "Pandora" returns every
#: legal name containing it, and emitting them all as claims would let the gate
#: accept whichever LEI happens to be unheld — a silent misattribution of an entire
#: filing history. Emitting none instead would leave the caller unable to tell
#: "no such entity" from "several, and I refused to choose".
WITHHELD_AMBIGUOUS_NAME_MATCH = "ambiguous_name_match"
#: One record matched, but only by containing the query, not by equalling it.
WITHHELD_PARTIAL_NAME_MATCH = "partial_name_match"
#: The registry has no record for this query.
WITHHELD_NOT_FOUND = "not_found"
#: The source cannot answer this shape of question at all — GLEIF has no ticker
#: index, so guessing a name from a ticker is how ``BA`` becomes Boeing.
WITHHELD_NOT_SUPPORTED = "not_supported"
#: SEC's ticker index does not cover this venue.
WITHHELD_VENUE_NOT_SEC_ELIGIBLE = "venue_not_sec_eligible"
#: The source failed — network, timeout, malformed payload. Recorded, never
#: silently turned into "no result", because those are different answers.
WITHHELD_SOURCE_ERROR = "source_error"

WITHHELD_REASONS: frozenset[str] = frozenset(
    {
        WITHHELD_AMBIGUOUS_NAME_MATCH,
        WITHHELD_PARTIAL_NAME_MATCH,
        WITHHELD_NOT_FOUND,
        WITHHELD_NOT_SUPPORTED,
        WITHHELD_VENUE_NOT_SEC_ELIGIBLE,
        WITHHELD_SOURCE_ERROR,
    }
)


@dataclass(frozen=True)
class WithheldFinding:
    """Something a source found but declined to assert, and why.

    Withheld is not the same as absent. "Six companies contain this name" is real
    information about the world, and a source that returns an empty list for it has
    thrown that information away.
    """

    scheme: str
    reason: str
    detail: str
    #: What was seen, for a human — candidate names, a status code. Never a claim.
    candidates: tuple[str, ...] = ()


@dataclass
class SourceLookupResult:
    """What one source produced for one query.

    Claims AND withheld findings, because a source's most useful answer is often
    "I found several and none of them is safe to assert". See ``WithheldFinding``
    and ``docs/v3/slices/V3.2-3.1-identifier-sources.md`` for why the protocol
    returns this rather than a bare list.
    """

    source_id: str
    claims: list[IdentifierClaim] = field(default_factory=list)
    withheld: list[WithheldFinding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.claims and not self.withheld


@runtime_checkable
class IdentifierSource(Protocol):
    """A place identifiers come from.

    ``lookup`` returns a ``SourceLookupResult`` rather than a list of claims. The
    reason is recorded in the slice document: a registry whose name search is a
    partial match must be able to say "several matched and I refused to choose",
    and neither a full list nor an empty one can express that safely.
    """

    source_id: str
    schemes: frozenset[str]

    async def lookup(
        self, query: IdentifierQuery
    ) -> SourceLookupResult: ...  # pragma: no cover - protocol


@dataclass
class StaticIdentifierSource:
    """The reference implementation: claims supplied up front.

    Every test uses this. A fake is not a lesser test here — what the resolution
    slice must prove is what the platform does with a claim, and a live client
    would only make that proof depend on a registry's uptime.
    """

    source_id: str
    schemes: frozenset[str]
    claims_by_ticker: dict[str, list[IdentifierClaim]] = field(default_factory=dict)
    claims_by_name: dict[str, list[IdentifierClaim]] = field(default_factory=dict)
    withheld_by_ticker: dict[str, list[WithheldFinding]] = field(default_factory=dict)
    #: Set to raise on lookup, so a caller's containment of a failing source is
    #: testable without a network.
    raises: Exception | None = None

    async def lookup(self, query: IdentifierQuery) -> SourceLookupResult:
        if self.raises is not None:
            raise self.raises
        found: list[IdentifierClaim] = []
        if query.ticker:
            found.extend(self.claims_by_ticker.get(query.ticker.strip().upper(), []))
        if query.legal_name:
            found.extend(self.claims_by_name.get(query.legal_name.strip().lower(), []))
        held: list[WithheldFinding] = []
        if query.ticker:
            held.extend(self.withheld_by_ticker.get(query.ticker.strip().upper(), []))
        return SourceLookupResult(
            source_id=self.source_id,
            claims=[c for c in found if c.scheme in self.schemes],
            withheld=held,
        )


async def verify_identifier_claim(
    session: Any,
    *,
    claim: IdentifierClaim,
    cfg: "Settings",
    legal_entity_id: uuid.UUID | None = None,
    security_id: uuid.UUID | None = None,
    verified_at: datetime | None = None,
) -> ClaimOutcome:
    """Put one claim through the gate. Flush-only; the caller commits.

    Returns a ``ClaimOutcome`` in every case — an accepted claim carries the row it
    created, a refused one carries the reason. It does not raise for a bad claim,
    because a bad claim is a normal, expected, *informative* outcome when the input
    is a third party's assertion.
    """
    if not getattr(cfg, "v3_entity_master_enabled", False):
        return ClaimOutcome(claim, False, REJECTED_DISABLED, "entity master is off")

    if not (claim.source or "").strip():
        return ClaimOutcome(
            claim,
            False,
            REJECTED_NO_SOURCE,
            "an unsourced identifier is a guess and is not stored",
        )

    try:
        ident = normalize_identifier(
            claim.scheme, claim.value, jurisdiction=claim.jurisdiction
        )
    except ValueError as exc:
        return ClaimOutcome(claim, False, REJECTED_INVALID_IDENTIFIER, str(exc))

    if ident.subject == SUBJECT_LEGAL_ENTITY and legal_entity_id is None:
        return ClaimOutcome(
            claim,
            False,
            REJECTED_SUBJECT_MISMATCH,
            f"{ident.scheme} identifies a legal person; no legal entity was named",
        )
    if ident.subject == SUBJECT_SECURITY and security_id is None:
        return ClaimOutcome(
            claim,
            False,
            REJECTED_SUBJECT_MISMATCH,
            f"{ident.scheme} identifies an instrument; no security was named",
        )

    if legal_entity_id is not None:
        exists = (
            await session.execute(
                select(LegalEntity.id).where(LegalEntity.id == legal_entity_id)
            )
        ).scalar_one_or_none()
        if exists is None:
            return ClaimOutcome(
                claim, False, REJECTED_SUBJECT_NOT_FOUND, "no such legal entity"
            )

    # The Boeing rule. A CIK is never derived from a ticker on a venue SEC's index
    # does not cover, whatever produced the claim.
    #
    # An entity with NO listing at all cannot be checked this way, and the rule does
    # not fire: "we cannot evaluate the venue" is not "the venue is disqualified",
    # and refusing every CIK for a listing-less entity would block the legitimate
    # case of an issuer known only by its LEI. The narrowing is deliberate and
    # pinned by a test, so it is a decision rather than an oversight.
    if ident.scheme == SCHEME_CIK and legal_entity_id is not None:
        blocked = await _non_sec_eligible_venues(session, legal_entity_id)
        eligible = await _has_sec_eligible_listing(session, legal_entity_id)
        if blocked and not eligible:
            return ClaimOutcome(
                claim,
                False,
                REJECTED_VENUE_NOT_SEC_ELIGIBLE,
                "this entity's only listings are on venues SEC's ticker index does "
                f"not cover ({', '.join(sorted(blocked))}); a CIK derived from a "
                "ticker there returns an unrelated US issuer",
            )

    holder = (
        await session.execute(
            select(EntityIdentifier).where(
                EntityIdentifier.scheme == ident.scheme,
                EntityIdentifier.value_normalized == ident.value_normalized,
                EntityIdentifier.scope_key == ident.scope_key,
                EntityIdentifier.effective_to.is_(None),
            )
        )
    ).scalar_one_or_none()
    if holder is not None and (
        holder.legal_entity_id != legal_entity_id or holder.security_id != security_id
    ):
        return ClaimOutcome(
            claim,
            False,
            REJECTED_HELD_BY_ANOTHER_ENTITY,
            f"{ident.scheme} {ident.value_normalized} is currently held by another "
            "subject; which is right is an evidence question and is not settled by "
            "overwriting",
        )

    record = await record_identifier(
        session,
        scheme=ident.scheme,
        value=ident.value,
        cfg=cfg,
        legal_entity_id=legal_entity_id,
        security_id=security_id,
        source=claim.source,
        source_url=claim.source_url,
        jurisdiction=claim.jurisdiction,
        confidence=claim.confidence,
        effective_from=claim.effective_from,
        verified_at=verified_at,
    )
    if record is None:  # pragma: no cover - the flag was checked above
        return ClaimOutcome(claim, False, REJECTED_DISABLED, "entity master is off")

    # Say what was actually checked. ``checksum_verified`` False is not a weaker
    # acceptance — it means the scheme has no check digits (CIK) or that its variant
    # is deliberately not validated (FIGI) — and a reader deciding how far to trust
    # the row needs the difference stated rather than inferred.
    checked = (
        "checksum verified"
        if ident.checksum_verified
        else "shape verified; this scheme carries no verified check digit"
    )
    detail = f"{checked}; source {claim.source}"
    if claim.detail:
        detail = f"{detail}; {claim.detail}"
    return ClaimOutcome(claim, True, None, detail, record.id)


def _listing_is_sec_eligible(code: str | None) -> bool:
    """Whether SEC's ticker index can authoritatively resolve THIS LISTING's venue.

    Deliberately NOT a straight call to ``exchange_registry.is_sec_eligible``, which
    returns **True** for ``None`` by design: that helper serves V2's legacy
    ticker-only flow, where an absent exchange means "no venue was supplied" and
    treating it as ineligible would regress every AAPL/MSFT lookup to "not sourced".

    A listing row is a different thing. It always has a venue; ``exchange_code``
    NULL means the platform holds a listing whose venue it *cannot name*. Inheriting
    the legacy default here would make the most dangerous rule in this module fail
    OPEN on its weakest possible input — an unnamed venue would read as
    SEC-eligible and a derived CIK would be accepted. So the empty case is decided
    here, explicitly, in the safe direction.
    """
    if not code or not code.strip():
        return False
    return is_sec_eligible(code)


async def _non_sec_eligible_venues(
    session: Any, legal_entity_id: uuid.UUID
) -> set[str]:
    rows = await _listing_exchange_codes(session, legal_entity_id)
    # A venue the platform cannot name is reported as "unknown" rather than dropped.
    return {
        (code or "unknown") for code in rows if not _listing_is_sec_eligible(code)
    }


async def _has_sec_eligible_listing(session: Any, legal_entity_id: uuid.UUID) -> bool:
    rows = await _listing_exchange_codes(session, legal_entity_id)
    return any(_listing_is_sec_eligible(code) for code in rows)


async def _listing_exchange_codes(
    session: Any, legal_entity_id: uuid.UUID
) -> list[str | None]:
    return list(
        (
            await session.execute(
                select(SecurityListing.exchange_code)
                .join(Security, Security.id == SecurityListing.security_id)
                .where(
                    Security.legal_entity_id == legal_entity_id,
                    SecurityListing.effective_to.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )


__all__ = [
    "REJECTED_DISABLED",
    "REJECTED_HELD_BY_ANOTHER_ENTITY",
    "REJECTED_INVALID_IDENTIFIER",
    "REJECTED_NO_SOURCE",
    "REJECTED_SUBJECT_MISMATCH",
    "REJECTED_SUBJECT_NOT_FOUND",
    "REJECTED_VENUE_NOT_SEC_ELIGIBLE",
    "REJECTION_REASONS",
    "ClaimOutcome",
    "IdentifierClaim",
    "IdentifierQuery",
    "IdentifierSource",
    "SourceLookupResult",
    "StaticIdentifierSource",
    "WITHHELD_AMBIGUOUS_NAME_MATCH",
    "WITHHELD_NOT_FOUND",
    "WITHHELD_NOT_SUPPORTED",
    "WITHHELD_PARTIAL_NAME_MATCH",
    "WITHHELD_REASONS",
    "WITHHELD_SOURCE_ERROR",
    "WITHHELD_VENUE_NOT_SEC_ELIGIBLE",
    "WithheldFinding",
    "verify_identifier_claim",
]
