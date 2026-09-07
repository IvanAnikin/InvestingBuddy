"""Data-classification and content policy for one corpus artifact — V3.1.

WHY THIS IS A VALUE, NOT A CODE BRANCH
======================================
Whether raw bytes may be kept, indexed, sent to an external model or quoted is
a property *of the material*, not of the code path that happened to fetch it.
``docs/v3/SECURITY_DATA_GOVERNANCE_AND_LICENSING.md`` §6 states it as six
independent permissions rather than one "is it public" boolean, precisely
because collapsing them is how a document reaches a vendor nobody evaluated.

So the policy travels with the artifact as data. A document that may not be
retained keeps its lineage and loses its bytes — the citation still resolves to
the canonical URL (``DATA_AND_EVIDENCE_ARCHITECTURE.md`` §2.2).

WHAT THIS MODULE DELIBERATELY DOES NOT DO
=========================================
It does not decide a licence. ``default_policy_for`` encodes the defaults table
in §6 of the governance document for the *public* classes, and denies by default
for everything else. A licensed or private artifact gets a deny-shaped default
that a caller must widen explicitly with a recorded reason; nothing here infers
that a fetch succeeded therefore retention is permitted ("we already fetched it
is not a licence", §10).

RETENTION IS CONFIGURABLE AND UNDECIDED
=======================================
`OPEN DECISION #12 <../../../../docs/v3/OPEN_DECISIONS.md>`_ (raw page and
document retention) is **user-owned and still open**: bytes indefinitely, bytes
with a TTL, or text only. This module therefore implements the *primitive* —
``retention_days`` → ``retention_expires_at`` — and defaults to ``0``, which
means "no TTL configured", never "free to keep forever as a policy". Expiry is
only ever applied by an explicit, callable sweep; nothing schedules one.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

# ── Data classes (governance §5) ──────────────────────────────────────────── #

ACCESS_PUBLIC_OFFICIAL = "public_official"
ACCESS_PUBLIC_ISSUER = "public_issuer"
ACCESS_PUBLIC_WEB = "public_web"
ACCESS_LICENSED_PRIVATE = "licensed_private"
ACCESS_USER_PRIVATE = "user_private"
ACCESS_DERIVED = "derived"

#: Every class the platform recognises. An unrecognised value is never treated
#: as public — see :func:`default_policy_for`.
ACCESS_CLASSES: frozenset[str] = frozenset(
    {
        ACCESS_PUBLIC_OFFICIAL,
        ACCESS_PUBLIC_ISSUER,
        ACCESS_PUBLIC_WEB,
        ACCESS_LICENSED_PRIVATE,
        ACCESS_USER_PRIVATE,
        ACCESS_DERIVED,
    }
)

#: The classes whose *content* is public by construction. Membership here is the
#: only thing that produces a permissive default.
PUBLIC_ACCESS_CLASSES: frozenset[str] = frozenset(
    {ACCESS_PUBLIC_OFFICIAL, ACCESS_PUBLIC_ISSUER, ACCESS_PUBLIC_WEB}
)

#: How much of an artifact may be quoted verbatim. ``bounded`` is the governance
#: table's entry for ``public_web``: the platform cites and excerpts, it does not
#: republish (§10).
QUOTE_FULL = "full"
QUOTE_BOUNDED = "bounded"
QUOTE_NONE = "none"

_ACCESS_CLASS_MAX = 30


@dataclass(frozen=True)
class ArtifactPolicy:
    """The six independent permissions of governance §6, plus retention.

    Every field is a permission the *artifact* carries, so a later tool boundary
    can ask "may this document go to this provider" per document rather than per
    run — a per-run flag is exactly the coarse control that leaks one document.
    """

    access_class: str
    #: May the raw bytes be persisted at all? ``False`` keeps lineage, drops bytes.
    stored: bool
    #: May its text enter a search index?
    indexed: bool
    #: May its text go to an external model at all? The **first** of two gates.
    #: OPEN DECISION #11 was resolved 2026-09-05 (ADR-049): the document's own rights
    #: decide, and geography does not. This stays fail-closed for every non-public
    #: class, so a private document is unusable by a model until somebody widens it
    #: explicitly — rights are never inferred from the fact that a file was uploaded.
    sent_to_external_model: bool
    #: ``full`` | ``bounded`` | ``none``.
    quoted: str
    #: May it outlive the run that fetched it?
    retained_long_term: bool
    #: ``None`` means no TTL is configured (OPEN DECISION #12), never "expired".
    retention_expires_at: datetime | None = None
    #: The **second** gate: which providers specifically. ``None`` means "any provider
    #: ``sent_to_external_model`` already allows"; a frozenset constrains to exactly
    #: those. ADR-049 requires both dimensions — "external models are allowed" and
    #: "*this* provider, under these terms, is allowed" are different questions, and a
    #: single boolean is the coarse control that leaks one document.
    #:
    #: An EMPTY frozenset is meaningful and distinct from ``None``: it means the
    #: document was considered and no provider was permitted.
    permitted_providers: frozenset[str] | None = None
    #: Why a non-public document was widened, and on whose authority. Required by
    #: :func:`allow_external_model`, because a grant nobody can audit is a grant
    #: nobody reviewed.
    external_model_rationale: str | None = None

    def __post_init__(self) -> None:
        if self.quoted not in (QUOTE_FULL, QUOTE_BOUNDED, QUOTE_NONE):
            raise ValueError(f"unknown quote policy: {self.quoted!r}")

    @property
    def may_store_bytes(self) -> bool:
        """Storing raw bytes needs BOTH permissions, not either one.

        ``stored`` is "may we write these bytes down"; ``retained_long_term`` is
        "may they outlive the run". An artifact that may be written but not
        retained has nowhere durable to live, so the honest answer is to keep the
        lineage and skip the bytes rather than to write something that must be
        deleted by a sweep that may never run.
        """
        return self.stored and self.retained_long_term

    def permits_provider(self, provider_id: str | None) -> bool:
        """Whether THIS document may go to THIS provider. Both gates, in order.

        Deliberately not "either gate": a document that may reach some external model
        is not thereby a document that may reach every one of them.
        """
        if not self.sent_to_external_model:
            return False
        if self.permitted_providers is None:
            return True
        return (provider_id or "") in self.permitted_providers

    def refuse_provider_reason(self, provider_id: str | None) -> str | None:
        """``None`` when permitted, else which of the two gates closed and why."""
        if not self.sent_to_external_model:
            return (
                f"this {self.access_class} document is not permitted to reach an "
                "external model at all; rights are not inferred from the fact that a "
                "file exists"
            )
        if self.permitted_providers is not None and (
            (provider_id or "") not in self.permitted_providers
        ):
            allowed = sorted(self.permitted_providers) or ["(none)"]
            return (
                f"this document names its permitted providers as {allowed} and "
                f"{provider_id!r} is not among them"
            )
        return None

    def is_expired(self, now: datetime) -> bool:
        """True only when a TTL was configured AND it has passed."""
        if self.retention_expires_at is None:
            return False
        return _as_aware(now) >= _as_aware(self.retention_expires_at)

    def with_expiry(self, expires_at: datetime | None) -> "ArtifactPolicy":
        return replace(self, retention_expires_at=expires_at)


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def normalize_access_class(raw: str | None) -> str:
    """Return a recognised access class, defaulting to the most restrictive.

    An unknown or missing class is ``licensed_private`` — deny-shaped — because
    the alternative (defaulting to public) means a mislabelled artifact silently
    acquires every permission. Fail closed.
    """
    value = (raw or "").strip().lower()[:_ACCESS_CLASS_MAX]
    if value in ACCESS_CLASSES:
        return value
    return ACCESS_LICENSED_PRIVATE


def allow_external_model(
    policy: ArtifactPolicy,
    *,
    rationale: str,
    providers: "frozenset[str] | set[str] | tuple[str, ...] | None" = None,
) -> ArtifactPolicy:
    """Widen a document so an external model may read it. Requires a rationale.

    The only supported way to make a non-public document usable by a provider, and it
    is deliberately awkward: a rationale is mandatory, so every widened document
    carries the reason it was widened and the reason is auditable later. ADR-049 states
    the rule this enforces — **rights are never inferred from the fact that a file was
    uploaded.**

    ``providers=None`` leaves the document open to any provider the coarse governance
    matrix already permits. Naming providers narrows it further, which is what a
    licence saying "may be processed by X" translates into.
    """
    if not (rationale or "").strip():
        raise ValueError(
            "widening a document for external models requires a rationale. A grant "
            "nobody can audit is a grant nobody reviewed."
        )
    allowed = None if providers is None else frozenset(providers)
    return replace(
        policy,
        sent_to_external_model=True,
        permitted_providers=allowed,
        external_model_rationale=rationale.strip(),
    )


def forbid_external_model(policy: ArtifactPolicy, *, rationale: str = "") -> ArtifactPolicy:
    """Close a document to every external model. Never requires a justification.

    Narrowing is always allowed and always safe, so unlike :func:`allow_external_model`
    this takes an optional rationale. The asymmetry is the point.
    """
    return replace(
        policy,
        sent_to_external_model=False,
        permitted_providers=frozenset(),
        external_model_rationale=(rationale.strip() or policy.external_model_rationale),
    )


def default_policy_for(
    access_class: str | None,
    *,
    retention_days: int = 0,
    now: datetime | None = None,
) -> ArtifactPolicy:
    """The governance §6 defaults table, as data.

    ``retention_days`` > 0 stamps an expiry; ``0`` leaves ``retention_expires_at``
    NULL, which reads as "no TTL configured" and is what keeps OPEN DECISION #12
    open rather than silently answering it.
    """
    resolved = normalize_access_class(access_class)
    expires_at = _expiry_from_days(retention_days, now=now)

    if resolved in PUBLIC_ACCESS_CLASSES:
        return ArtifactPolicy(
            access_class=resolved,
            stored=True,
            indexed=True,
            sent_to_external_model=True,
            # Third-party web pages are cited and excerpted, never republished.
            quoted=QUOTE_BOUNDED if resolved == ACCESS_PUBLIC_WEB else QUOTE_FULL,
            retained_long_term=True,
            retention_expires_at=expires_at,
        )

    if resolved == ACCESS_USER_PRIVATE:
        # The user's own document: storable and indexable for the owner, quotable
        # back to the owner, and NEVER sent to an external model by default.
        return ArtifactPolicy(
            access_class=resolved,
            stored=True,
            indexed=True,
            sent_to_external_model=False,
            quoted=QUOTE_FULL,
            retained_long_term=True,
            retention_expires_at=expires_at,
        )

    if resolved == ACCESS_DERIVED:
        # Derived material inherits the most restrictive input class, so a bare
        # default cannot be permissive: the caller must supply the inherited
        # policy. See :func:`derive_policy`.
        return ArtifactPolicy(
            access_class=resolved,
            stored=True,
            indexed=True,
            sent_to_external_model=False,
            quoted=QUOTE_BOUNDED,
            retained_long_term=True,
            retention_expires_at=expires_at,
        )

    # licensed_private, and anything unrecognised: licence-dependent in the
    # governance table, which as a *default* means deny.
    return ArtifactPolicy(
        access_class=resolved,
        stored=False,
        indexed=False,
        sent_to_external_model=False,
        quoted=QUOTE_NONE,
        retained_long_term=False,
        retention_expires_at=expires_at,
    )


def derive_policy(inputs: "list[ArtifactPolicy] | tuple[ArtifactPolicy, ...]") -> ArtifactPolicy:
    """The policy of something computed FROM other artifacts.

    Governance §6: *derived inherits the most restrictive input class*. Without
    this rule derivation launders classification — which is the most likely way
    private material would actually leak: not as a document, but as a sentence
    about one.

    With no inputs the result is deny-shaped, because "derived from nothing" is
    not a claim this platform makes about provenance.
    """
    if not inputs:
        return default_policy_for(ACCESS_LICENSED_PRIVATE)
    quote_rank = {QUOTE_NONE: 0, QUOTE_BOUNDED: 1, QUOTE_FULL: 2}
    quoted = min((p.quoted for p in inputs), key=lambda q: quote_rank[q])
    expiries = [p.retention_expires_at for p in inputs if p.retention_expires_at is not None]
    return ArtifactPolicy(
        access_class=ACCESS_DERIVED,
        stored=all(p.stored for p in inputs),
        indexed=all(p.indexed for p in inputs),
        sent_to_external_model=all(p.sent_to_external_model for p in inputs),
        quoted=quoted,
        retained_long_term=all(p.retained_long_term for p in inputs),
        # The soonest expiry wins: a derivation must not outlive its shortest-lived
        # input.
        retention_expires_at=min(expiries) if expiries else None,
    )


def _expiry_from_days(retention_days: int, *, now: datetime | None = None) -> datetime | None:
    days = int(retention_days or 0)
    if days <= 0:
        return None
    base = _as_aware(now or datetime.now(timezone.utc))
    return base + timedelta(days=days)


__all__ = [
    "allow_external_model",
    "forbid_external_model",
    "ACCESS_CLASSES",
    "ACCESS_DERIVED",
    "ACCESS_LICENSED_PRIVATE",
    "ACCESS_PUBLIC_ISSUER",
    "ACCESS_PUBLIC_OFFICIAL",
    "ACCESS_PUBLIC_WEB",
    "ACCESS_USER_PRIVATE",
    "PUBLIC_ACCESS_CLASSES",
    "QUOTE_BOUNDED",
    "QUOTE_FULL",
    "QUOTE_NONE",
    "ArtifactPolicy",
    "default_policy_for",
    "derive_policy",
    "normalize_access_class",
]
