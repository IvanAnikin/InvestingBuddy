"""Closed vocabularies for the entity master — V3.2 Slice 2.1.

Every set here is closed, and a value outside one **raises** rather than being
stored. That is the same rule ``fact_scope`` applies to scope: a label the
vocabulary does not recognise is not quietly promoted to the nearest thing that
looks like it.

The cost of an open vocabulary in this schema is specific. ``security_type`` is
what tells a later reader whether a price series belongs to an ordinary share or
to an ADR trading at a different ratio in a different currency; a free-text value
means every consumer re-implements that judgement from a string, which is how
``scope`` became three incompatible interpretations in three layers before
``fact_scope`` existed.

RESOLUTION STATES LIVE HERE, THE RESOLVER DOES NOT
==================================================
``RESOLUTION_STATES`` is defined in this slice because it is the vocabulary the
schema and slice 2.3 must agree on, and defining it twice is how they would come
to disagree. Nothing in slice 2.1 resolves anything — see
``docs/v3/slices/V3.2-1-entity-master.md``.
"""

from __future__ import annotations

from app.services.sources.fact_scope import SCOPE_TYPE_GROUP, SCOPE_TYPE_SEGMENT

# ── Security types ───────────────────────────────────────────────────────── #

SECURITY_ORDINARY_SHARE = "ordinary_share"
SECURITY_PREFERENCE_SHARE = "preference_share"
SECURITY_CLASS_SHARE = "class_share"
SECURITY_ADR = "adr"
SECURITY_GDR = "gdr"
SECURITY_UNIT = "unit"
SECURITY_OTHER = "other"

SECURITY_TYPES: frozenset[str] = frozenset(
    {
        SECURITY_ORDINARY_SHARE,
        SECURITY_PREFERENCE_SHARE,
        SECURITY_CLASS_SHARE,
        SECURITY_ADR,
        SECURITY_GDR,
        SECURITY_UNIT,
        SECURITY_OTHER,
    }
)

#: Depositary receipts represent shares held elsewhere, usually at a ratio, and
#: their financials are the *underlying* issuer's. A consumer that treats one as
#: an ordinary share silently applies the wrong per-share arithmetic, so the
#: distinction is named rather than left to a reader's inspection of the ticker.
DEPOSITARY_RECEIPT_TYPES: frozenset[str] = frozenset({SECURITY_ADR, SECURITY_GDR})

# ── Listing status ───────────────────────────────────────────────────────── #

LISTING_ACTIVE = "active"
LISTING_SUSPENDED = "suspended"
LISTING_DELISTED = "delisted"
LISTING_UNKNOWN = "unknown"

LISTING_STATUSES: frozenset[str] = frozenset(
    {LISTING_ACTIVE, LISTING_SUSPENDED, LISTING_DELISTED, LISTING_UNKNOWN}
)

# ── Entity status ────────────────────────────────────────────────────────── #

ENTITY_ACTIVE = "active"
ENTITY_INACTIVE = "inactive"
ENTITY_DISSOLVED = "dissolved"
ENTITY_UNKNOWN = "unknown"

ENTITY_STATUSES: frozenset[str] = frozenset(
    {ENTITY_ACTIVE, ENTITY_INACTIVE, ENTITY_DISSOLVED, ENTITY_UNKNOWN}
)

#: The default. "We have not established the entity's registration status" is a
#: real answer and is never upgraded to ``active`` because a document was found —
#: a dissolved entity's last annual report is still fetchable.
DEFAULT_ENTITY_STATUS = ENTITY_UNKNOWN

# ── Alias types ──────────────────────────────────────────────────────────── #

ALIAS_FORMER_LEGAL_NAME = "former_legal_name"
ALIAS_TRADE_NAME = "trade_name"
ALIAS_SHORT_NAME = "short_name"
ALIAS_TRANSLITERATION = "transliteration"
ALIAS_FORMER_TICKER = "former_ticker"

ALIAS_TYPES: frozenset[str] = frozenset(
    {
        ALIAS_FORMER_LEGAL_NAME,
        ALIAS_TRADE_NAME,
        ALIAS_SHORT_NAME,
        ALIAS_TRANSLITERATION,
        ALIAS_FORMER_TICKER,
    }
)

# ── Resolution states (vocabulary only; the resolver is slice 2.3) ───────── #

#: Exactly one entity matched on evidence strong enough to act on.
RESOLUTION_RESOLVED = "resolved"
#: More than one candidate matched and no evidence separates them. This is a
#: research GAP, not a tie to be broken — see the split-over-merge rule.
RESOLUTION_AMBIGUOUS = "ambiguous"
#: Candidates matched and their evidence actively disagrees (two sources give the
#: same ticker two different LEIs). Stronger than ambiguous: something upstream
#: is wrong and a human should see it.
RESOLUTION_CONFLICTING = "conflicting"
#: Nothing matched. Not an error — a company the platform has never seen.
RESOLUTION_UNRESOLVED = "unresolved"

RESOLUTION_STATES: frozenset[str] = frozenset(
    {
        RESOLUTION_RESOLVED,
        RESOLUTION_AMBIGUOUS,
        RESOLUTION_CONFLICTING,
        RESOLUTION_UNRESOLVED,
    }
)

#: The states in which a caller must NOT proceed as if it knew the entity.
#: Membership is what "never silently merge" reduces to at a call site.
NON_ACTIONABLE_RESOLUTION_STATES: frozenset[str] = frozenset(
    {RESOLUTION_AMBIGUOUS, RESOLUTION_CONFLICTING, RESOLUTION_UNRESOLVED}
)


# ── Guards ───────────────────────────────────────────────────────────────── #


def _require(value: str | None, allowed: frozenset[str], what: str) -> str:
    key = (value or "").strip().lower()
    if key not in allowed:
        raise ValueError(
            f"{value!r} is not a recognised {what}. "
            f"Recognised: {', '.join(sorted(allowed))}."
        )
    return key


def require_security_type(value: str | None) -> str:
    return _require(value, SECURITY_TYPES, "security type")


def require_listing_status(value: str | None) -> str:
    return _require(value, LISTING_STATUSES, "listing status")


def require_entity_status(value: str | None) -> str:
    return _require(value, ENTITY_STATUSES, "entity status")


def require_alias_type(value: str | None) -> str:
    return _require(value, ALIAS_TYPES, "alias type")


def require_resolution_state(value: str | None) -> str:
    return _require(value, RESOLUTION_STATES, "resolution state")


def is_depositary_receipt(security_type: str | None) -> bool:
    """True when per-share arithmetic must not be applied without a ratio."""
    return (security_type or "").strip().lower() in DEPOSITARY_RECEIPT_TYPES


# ── Relationship types (V3.2 Slice 2.4) ──────────────────────────────────── #

#: ``subject`` is the parent of ``object``.
REL_PARENT_OF = "parent_of"
#: ``subject`` was renamed/reorganised INTO ``object`` — subject came first.
REL_PREDECESSOR_OF = "predecessor_of"
#: ``subject`` and ``object`` are partners in a joint venture. Symmetric.
REL_JOINT_VENTURE_WITH = "joint_venture_with"
#: ``subject`` issues depositary receipts over ``object``'s shares. Used only for the
#: rare cross-entity depositary arrangement; the ordinary ADR case is an
#: instrument-level link (``securities.underlying_security_id``), because a
#: depositary receipt represents underlying SHARES at a ratio, not a company.
REL_DEPOSITARY_FOR = "depositary_for"

#: The relationship types actually stored. Every other direction is a QUERY.
#:
#: ``parent_of`` and ``subsidiary_of`` are inverses, and storing both invites two
#: rows that contradict each other with no rule for which wins. So the vocabulary
#: declares one canonical direction per pair and the writer normalises to it.
CANONICAL_RELATIONSHIP_TYPES: frozenset[str] = frozenset(
    {
        REL_PARENT_OF,
        REL_PREDECESSOR_OF,
        REL_JOINT_VENTURE_WITH,
        REL_DEPOSITARY_FOR,
    }
)

#: Types a caller may assert, mapped to ``(canonical_type, swap_subject_object)``.
#: Asserting ``subsidiary_of(A, B)`` stores ``parent_of(B, A)``.
RELATIONSHIP_ALIASES: dict[str, tuple[str, bool]] = {
    REL_PARENT_OF: (REL_PARENT_OF, False),
    "subsidiary_of": (REL_PARENT_OF, True),
    REL_PREDECESSOR_OF: (REL_PREDECESSOR_OF, False),
    "successor_of": (REL_PREDECESSOR_OF, True),
    REL_JOINT_VENTURE_WITH: (REL_JOINT_VENTURE_WITH, False),
    REL_DEPOSITARY_FOR: (REL_DEPOSITARY_FOR, False),
    "depositary_receipt_of": (REL_DEPOSITARY_FOR, True),
}

#: Types where the direction carries no meaning, so a query must look both ways.
SYMMETRIC_RELATIONSHIP_TYPES: frozenset[str] = frozenset({REL_JOINT_VENTURE_WITH})

#: How to describe the reverse of a stored row to a human.
RELATIONSHIP_INVERSE_LABEL: dict[str, str] = {
    REL_PARENT_OF: "subsidiary_of",
    REL_PREDECESSOR_OF: "successor_of",
    REL_JOINT_VENTURE_WITH: REL_JOINT_VENTURE_WITH,
    REL_DEPOSITARY_FOR: "depositary_receipt_of",
}


# ── Reporting scope types (V3.2 Slice 2.4) ───────────────────────────────── #
#
# ``group`` and ``segment`` are ``fact_scope``'s own two types and are imported from
# there rather than redefined, so the two vocabularies cannot drift. ``region`` and
# ``division`` are the additional levels the architecture document names, which a
# segment table alone cannot express.

SCOPE_REGION = "region"
SCOPE_DIVISION = "division"

#: Every scope type a ``reporting_scopes`` row may carry.
REPORTING_SCOPE_TYPES: frozenset[str] = frozenset(
    {SCOPE_TYPE_GROUP, SCOPE_TYPE_SEGMENT, SCOPE_REGION, SCOPE_DIVISION}
)


def require_relationship_type(value: str | None) -> tuple[str, bool]:
    """Resolve an asserted type to ``(canonical_type, swap)``, or raise."""
    key = (value or "").strip().lower()
    resolved = RELATIONSHIP_ALIASES.get(key)
    if resolved is None:
        raise ValueError(
            f"{value!r} is not a recognised relationship type. Recognised: "
            f"{', '.join(sorted(RELATIONSHIP_ALIASES))}."
        )
    return resolved


def require_reporting_scope_type(value: str | None) -> str:
    return _require(value, REPORTING_SCOPE_TYPES, "reporting scope type")


def is_symmetric_relationship(canonical_type: str | None) -> bool:
    return (canonical_type or "").strip().lower() in SYMMETRIC_RELATIONSHIP_TYPES
