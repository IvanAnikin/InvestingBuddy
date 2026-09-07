"""Provider-neutral universe generation — V3.2 Slice 2.5.

Turns "which companies are in scope for this thesis?" from *one curated table* into
a composition of sources, de-duplicated by **listing identity** rather than by ticker
string, with every member recording which provider supplied it.

WHY IDENTITY, NOT A TICKER STRING
=================================
This is the first slice that can do it. Before the entity master, two candidates for
``BA`` were the same universe member or two, depending on whether whoever wrote the
de-duplication happened to include the exchange — and this repository has the live
incident to prove which way that goes wrong. Here ``BA`` on ``XLON`` and ``BA`` on
``XNYS`` are two members by construction, because the key is
``(venue_key, ticker)`` and the venue comes from ``exchange_registry``.

TWO CANDIDATES MERGE ONLY ON EVIDENCE
=====================================
Two candidates become one member when they share a listing identity, or when both
**resolve** to the same legal entity with an actionable state. If either resolution is
`ambiguous`, `conflicting` or `unresolved`, they stay separate. A duplicate universe
member costs one wasted research slot; a wrong merge researches one company under
another's name. Over-split, as everywhere else in V3.2.

An unresolved candidate is **not excluded** — a company the platform has never seen
is exactly what a universe is for. It enters with ``resolution_state`` recorded and
``legal_entity_id`` NULL, so nothing downstream can mistake it for an identified
issuer.

THE CAP IS THE POINT, NOT A DETAIL
==================================
``HARD_MAX_UNIVERSE_SIZE`` (50) is enforced **before** anything expensive touches a
candidate. A full company research run measures 261-451 seconds, so a universe of a
thousand companies is not a slow feature — it is an outage, and on the deployed B1
plan five concurrent analyses already exceed the stale threshold. Nothing in this
module triggers analysis, enrichment or a network call; it produces a bounded search
space and a record of how it was bounded.

WITH THE FLAG OFF, NOTHING CHANGES
==================================
``V3_UNIVERSE_PROVIDERS_ENABLED`` defaults off, and with it off callers keep using
``market_universe_builder.build_universe`` directly. The curated registry is demoted
to one provider, not deleted — it is the only source with a hand-verified reason for
every entry, and it stays until a validated replacement exists.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from app.services.entities.master import venue_key_for
from app.services.entities.vocabulary import (
    NON_ACTIONABLE_RESOLUTION_STATES,
    RESOLUTION_UNRESOLVED,
)
from app.services.market_universe_builder import (
    DEFAULT_MAX_UNIVERSE_SIZE,
    HARD_MAX_UNIVERSE_SIZE,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings


@dataclass
class UniverseRequest:
    """What universe is being asked for.

    ``parsed_thesis`` is the existing ``market_thesis_parser`` output, passed through
    unchanged so a provider that only understands themes keeps working.
    """

    parsed_thesis: dict[str, Any] = field(default_factory=dict)
    max_size: int = DEFAULT_MAX_UNIVERSE_SIZE

    @property
    def cap(self) -> int:
        """The effective bound. Never above the hard ceiling, never below 1."""
        return max(1, min(int(self.max_size or DEFAULT_MAX_UNIVERSE_SIZE),
                          HARD_MAX_UNIVERSE_SIZE))


@dataclass
class UniverseCandidate:
    """One company a provider proposes, with its identity as far as it is known."""

    ticker: str
    exchange: str
    company_name: str | None = None
    country: str | None = None
    region: str | None = None
    sector: str | None = None
    industry: str | None = None
    theme: str | None = None
    matched_keywords: list[str] = field(default_factory=list)
    relevance_reason: str = ""
    relevance_score_pre_scan: float = 0.0
    #: Which provider proposed it, and that source's tier. Never inferred.
    universe_source: str = ""
    source_tier: str = ""
    metadata_not_sourced: bool = False
    warnings: list[str] = field(default_factory=list)
    #: Set by the composer, never by a provider: identity is the platform's to decide.
    legal_entity_id: uuid.UUID | None = None
    resolution_state: str = RESOLUTION_UNRESOLVED
    #: Every provider that proposed this member, in order.
    contributing_sources: list[str] = field(default_factory=list)

    @property
    def listing_key(self) -> tuple[str, str]:
        """``(venue_key, ticker)`` — the identity two proposals share to be one member.

        The venue comes from ``exchange_registry``, so two providers naming the same
        venue differently still produce one member, and two issuers sharing a ticker
        on different venues never do.
        """
        return (venue_key_for(self.exchange), (self.ticker or "").strip().upper())

    @property
    def is_identified(self) -> bool:
        """True only when a legal entity was resolved on actionable evidence."""
        return (
            self.legal_entity_id is not None
            and self.resolution_state not in NON_ACTIONABLE_RESOLUTION_STATES
        )

    def to_universe_item_dict(self) -> dict[str, Any]:
        """The shape the existing Phase 25 discovery scan already consumes.

        The V3 fields are added, not substituted, so a downstream reader that knows
        nothing about entities keeps working and one that does can check
        ``is_identified`` before treating the member as a known issuer.
        """
        return {
            "ticker": self.ticker,
            "company_name": self.company_name,
            "exchange": self.exchange,
            "country": self.country,
            "region": self.region,
            "sector": self.sector,
            "industry": self.industry,
            "theme": self.theme,
            "matched_keywords": list(self.matched_keywords),
            "relevance_reason": self.relevance_reason,
            "universe_source": self.universe_source,
            "source_tier": self.source_tier,
            "relevance_score_pre_scan": self.relevance_score_pre_scan,
            "metadata_not_sourced": self.metadata_not_sourced,
            "warnings": list(self.warnings),
            "legal_entity_id": (
                str(self.legal_entity_id) if self.legal_entity_id else None
            ),
            "resolution_state": self.resolution_state,
            "contributing_sources": list(self.contributing_sources),
        }


@dataclass
class ProviderContribution:
    """What one provider produced, kept per-provider so attribution survives."""

    provider_id: str
    source_tier: str
    proposed: int = 0
    accepted: int = 0
    #: Members this provider proposed that another had already contributed.
    merged_into_existing: int = 0
    excluded: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class UniverseSet:
    """A bounded universe and the full record of how it was bounded."""

    members: list[UniverseCandidate] = field(default_factory=list)
    excluded: list[dict[str, Any]] = field(default_factory=list)
    contributions: list[ProviderContribution] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    needs_narrowing: bool = False
    cap: int = DEFAULT_MAX_UNIVERSE_SIZE
    #: True when the cap stopped the universe rather than the providers running out.
    truncated_by_cap: bool = False

    @property
    def identified_members(self) -> list[UniverseCandidate]:
        return [m for m in self.members if m.is_identified]

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [m.to_universe_item_dict() for m in self.members],
            "excluded": list(self.excluded),
            "source_summary": {
                "selected": len(self.members),
                "excluded": len(self.excluded),
                "identified": len(self.identified_members),
                "providers": [
                    {
                        "provider_id": c.provider_id,
                        "source_tier": c.source_tier,
                        "proposed": c.proposed,
                        "accepted": c.accepted,
                        "merged_into_existing": c.merged_into_existing,
                        "excluded": len(c.excluded),
                        "error": c.error,
                    }
                    for c in self.contributions
                ],
            },
            "warnings": list(self.warnings),
            "needs_narrowing": self.needs_narrowing,
            "requested_max": self.cap,
            "truncated_by_cap": self.truncated_by_cap,
        }


@dataclass
class ProviderResult:
    """One provider's answer: candidates, exclusions with reasons, warnings."""

    candidates: list[UniverseCandidate] = field(default_factory=list)
    excluded: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    needs_narrowing: bool = False


@runtime_checkable
class UniverseProvider(Protocol):
    """A source of universe candidates."""

    provider_id: str
    source_tier: str

    async def candidates(self, request: UniverseRequest) -> ProviderResult:
        ...  # pragma: no cover - protocol


async def build_universe_from_providers(
    providers: Sequence[UniverseProvider],
    request: UniverseRequest,
    *,
    cfg: "Settings",
    resolver: Any | None = None,
    session: Any | None = None,
) -> UniverseSet:
    """Compose a bounded universe from several providers.

    ``resolver`` is an optional ``async (session, EntityQuery, cfg) -> outcome``
    callable — normally ``entities.resolution.resolve``. Injected rather than imported
    so this module has no opinion about when identity is consulted, and so a caller
    with no database still gets a universe.

    Returns an empty set — and consults no provider — when the flag is off. Nothing
    here fetches, enriches or analyses.
    """
    result = UniverseSet(cap=request.cap)
    if not getattr(cfg, "v3_universe_providers_enabled", False):
        result.warnings.append(
            "universe providers are disabled; callers use "
            "market_universe_builder.build_universe unchanged"
        )
        return result

    if request.parsed_thesis.get("needs_narrowing"):
        # The existing guardrail, preserved: a vague thesis produces no universe and
        # the caller must refuse to launch a scan.
        result.needs_narrowing = True
        result.warnings.extend(
            list(request.parsed_thesis.get("warnings") or [])
            or ["Thesis needs narrowing — no bounded universe built."]
        )
        return result

    by_listing: dict[tuple[str, str], UniverseCandidate] = {}
    by_entity: dict[uuid.UUID, UniverseCandidate] = {}

    for provider in providers:
        contribution = ProviderContribution(
            provider_id=getattr(provider, "provider_id", provider.__class__.__name__),
            source_tier=getattr(provider, "source_tier", ""),
        )
        result.contributions.append(contribution)
        try:
            produced = await provider.candidates(request)
        except Exception as exc:  # noqa: BLE001 - one source must not stop the rest
            contribution.error = f"{type(exc).__name__}: {exc}"
            result.warnings.append(
                f"provider {contribution.provider_id} failed and contributed nothing: "
                f"{contribution.error}"
            )
            continue

        contribution.proposed = len(produced.candidates)
        contribution.warnings.extend(produced.warnings)
        contribution.excluded.extend(produced.excluded)
        result.excluded.extend(produced.excluded)
        result.warnings.extend(produced.warnings)
        if produced.needs_narrowing:
            result.needs_narrowing = True

        for candidate in produced.candidates:
            candidate.universe_source = (
                candidate.universe_source or contribution.provider_id
            )
            candidate.source_tier = candidate.source_tier or contribution.source_tier
            if contribution.provider_id not in candidate.contributing_sources:
                candidate.contributing_sources.append(contribution.provider_id)

            # The MERGE check comes before the cap check, deliberately. A candidate
            # that merges into a member already accepted adds no member, so refusing
            # it for "cap reached" would throw away information the universe could
            # absorb for free — and report a spurious exclusion for a company that is
            # in fact present.
            key = candidate.listing_key
            existing = by_listing.get(key)
            if existing is not None:
                # Same venue, same ticker — the same listing, so the same member.
                if contribution.provider_id not in existing.contributing_sources:
                    existing.contributing_sources.append(contribution.provider_id)
                _fill_gaps(existing, candidate)
                contribution.merged_into_existing += 1
                continue

            if len(by_listing) >= request.cap:
                # The cap stops the universe BEFORE anything expensive sees it —
                # including before identity is resolved, which is the only query in
                # this loop.
                result.truncated_by_cap = True
                contribution.excluded.append(
                    {
                        "ticker": candidate.ticker,
                        "company_name": candidate.company_name,
                        "reason": f"universe cap of {request.cap} already reached",
                    }
                )
                result.excluded.append(contribution.excluded[-1])
                continue

            await _resolve_identity(
                candidate, resolver=resolver, session=session, cfg=cfg
            )

            if candidate.is_identified and candidate.legal_entity_id is not None:
                same_entity = by_entity.get(candidate.legal_entity_id)
                if same_entity is not None:
                    # Two listings of ONE issuer. Merging is safe here and only here:
                    # both sides resolved to the same entity on actionable evidence.
                    if contribution.provider_id not in same_entity.contributing_sources:
                        same_entity.contributing_sources.append(
                            contribution.provider_id
                        )
                    _fill_gaps(same_entity, candidate)
                    contribution.merged_into_existing += 1
                    continue
                by_entity[candidate.legal_entity_id] = candidate

            by_listing[key] = candidate
            contribution.accepted += 1

    result.members = list(by_listing.values())
    return result


def _fill_gaps(target: UniverseCandidate, other: UniverseCandidate) -> None:
    """Fill blanks on an accepted member from a later proposal of the same company.

    Fills only what is missing. A second provider that knows less must not overwrite
    what a better one established — the same rule ``upsert_legal_entity`` follows, and
    the reason a curated entry's hand-verified reason is not replaced by a generated
    one.
    """
    target.company_name = target.company_name or other.company_name
    target.country = target.country or other.country
    target.region = target.region or other.region
    target.sector = target.sector or other.sector
    target.industry = target.industry or other.industry
    target.theme = target.theme or other.theme
    if not target.relevance_reason:
        target.relevance_reason = other.relevance_reason
    for keyword in other.matched_keywords:
        if keyword not in target.matched_keywords:
            target.matched_keywords.append(keyword)
    target.relevance_score_pre_scan = max(
        target.relevance_score_pre_scan, other.relevance_score_pre_scan
    )
    # `metadata_not_sourced` may only be CLEARED, never set: another provider knowing
    # less is not evidence that the metadata is unavailable.
    if other.company_name and other.sector:
        target.metadata_not_sourced = False
    for warning in other.warnings:
        if warning not in target.warnings:
            target.warnings.append(warning)


async def _resolve_identity(
    candidate: UniverseCandidate,
    *,
    resolver: Any | None,
    session: Any | None,
    cfg: "Settings",
) -> None:
    """Attach a legal entity when one resolves on actionable evidence.

    A failure to resolve leaves the candidate in the universe with its state recorded.
    A company the platform has never seen is exactly what a universe is for; what
    must not happen is it being treated as identified.
    """
    if resolver is None or session is None:
        return
    from app.services.entities.resolution import EntityQuery

    try:
        outcome = await resolver(
            session,
            EntityQuery(
                ticker=candidate.ticker,
                exchange=candidate.exchange,
                legal_name=candidate.company_name,
            ),
            cfg=cfg,
        )
    except Exception as exc:  # noqa: BLE001 - identity is advisory to a search space
        candidate.warnings.append(f"identity resolution failed: {type(exc).__name__}")
        return

    candidate.resolution_state = getattr(outcome, "state", RESOLUTION_UNRESOLVED)
    entity = getattr(outcome, "entity", None)
    if entity is not None and getattr(outcome, "is_actionable", False):
        candidate.legal_entity_id = entity.id
        candidate.company_name = candidate.company_name or entity.legal_name


__all__ = [
    "DEFAULT_MAX_UNIVERSE_SIZE",
    "HARD_MAX_UNIVERSE_SIZE",
    "ProviderContribution",
    "ProviderResult",
    "UniverseCandidate",
    "UniverseProvider",
    "UniverseRequest",
    "UniverseSet",
    "build_universe_from_providers",
]
