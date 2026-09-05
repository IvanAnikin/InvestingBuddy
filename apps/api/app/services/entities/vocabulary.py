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
