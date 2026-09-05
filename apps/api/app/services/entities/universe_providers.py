"""Universe providers — V3.2 Slice 2.5.

Two sources, both deterministic and both offline. Between them they cover the two
questions a universe has to answer: *what could be relevant to this thesis* and *what
does this platform already know about*.

THE CURATED REGISTRY IS DEMOTED, NOT DELETED
============================================
``market_universe_builder.THEME_COMPANY_REGISTRY`` stops being the definition of the
universe and becomes one provider. It is not removed and should not be: it is the only
source with a hand-verified reason for every entry, and V3's own rule is that nothing
is deleted before a validated replacement exists. Its guardrails — the hard cap, the
region/exclusion filters, the recorded exclusion reasons, the refusal on a vague
thesis — are reused rather than reimplemented, so the deterministic filter stage of
the funnel keeps behaving exactly as it does today.

THE ENTITY MASTER AS A UNIVERSE
===============================
Every company the platform has ever researched has a ``SecurityListing``. That is a
universe with real identity attached, and it is the one source that improves on its
own as the platform is used — a company researched last month is a candidate this
month without anybody adding it to a table. It is deliberately the *second* provider:
its candidates carry identity but no thesis relevance, so the curated source's
reasons come first and this one fills in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.legal_entity import LegalEntity, Security, SecurityListing
from app.services.entities.universe import (
    ProviderResult,
    UniverseCandidate,
    UniverseRequest,
)
from app.services.exchange_registry import (
    country_for_exchange,
    region_for_country,
)
from app.services.market_universe_builder import build_universe

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings

CURATED_PROVIDER_ID = "curated_theme_registry"
CURATED_SOURCE_TIER = "T3_curated_reference_list"
ENTITY_MASTER_PROVIDER_ID = "entity_master_listings"
#: The platform's own accumulated record. Derived, not a third party's assertion —
#: which is why it is not a T5 aggregator tier.
ENTITY_MASTER_SOURCE_TIER = "T4_platform_record"


@dataclass
class CuratedRegistryUniverseProvider:
    """The existing deterministic builder, as one provider.

    Delegates to ``market_universe_builder.build_universe`` rather than reaching into
    the registry, so the region and exclusion filters, the recorded exclusion reasons
    and the pre-scan relevance score are the *same code* that runs today. A second
    implementation of a filter is a second set of rules to keep in agreement.
    """

    provider_id: str = CURATED_PROVIDER_ID
    source_tier: str = CURATED_SOURCE_TIER

    async def candidates(self, request: UniverseRequest) -> ProviderResult:
        built = build_universe(
            request.parsed_thesis, max_universe_size=request.cap
        )
        out = ProviderResult(
            excluded=list(built.excluded),
            warnings=list(built.warnings),
            needs_narrowing=built.needs_narrowing,
        )
        for item in built.items:
            out.candidates.append(
                UniverseCandidate(
                    ticker=str(item.get("ticker") or ""),
                    exchange=str(item.get("exchange") or ""),
                    company_name=item.get("company_name"),
                    country=item.get("country"),
                    region=item.get("region"),
                    sector=item.get("sector"),
                    industry=item.get("industry"),
                    theme=item.get("theme"),
                    matched_keywords=list(item.get("matched_keywords") or []),
                    relevance_reason=str(item.get("relevance_reason") or ""),
                    relevance_score_pre_scan=float(
                        item.get("relevance_score_pre_scan") or 0.0
                    ),
                    universe_source=str(item.get("universe_source") or self.provider_id),
                    source_tier=str(item.get("source_tier") or self.source_tier),
                    metadata_not_sourced=bool(item.get("metadata_not_sourced")),
                    warnings=list(item.get("warnings") or []),
                )
            )
        return out


@dataclass
class EntityMasterUniverseProvider:
    """Every issuer with a current listing in the entity master.

    ``session`` is injected. Bounded by the request cap in the query itself, not after
    the fact: a universe provider that loads the whole table and slices it has already
    paid for the scan the cap exists to prevent.

    It carries **no thesis relevance**, and says so — ``relevance_reason`` records that
    the member came from the platform's own record rather than from a match. Claiming a
    relevance it did not compute would let a downstream ranking treat "we have seen
    this company" as "this company fits the thesis".
    """

    session: Any
    cfg: "Settings"
    provider_id: str = ENTITY_MASTER_PROVIDER_ID
    source_tier: str = ENTITY_MASTER_SOURCE_TIER
    #: Restrict to these venue codes when set; empty means every venue.
    exchange_codes: tuple[str, ...] = field(default_factory=tuple)

    async def candidates(self, request: UniverseRequest) -> ProviderResult:
        out = ProviderResult()
        if not getattr(self.cfg, "v3_entity_master_enabled", False):
            out.warnings.append(
                f"{self.provider_id}: the entity master is disabled, so it holds no "
                "universe"
            )
            return out

        stmt = (
            select(SecurityListing, LegalEntity)
            .join(Security, Security.id == SecurityListing.security_id)
            .join(LegalEntity, LegalEntity.id == Security.legal_entity_id)
            .where(SecurityListing.effective_to.is_(None))
            .order_by(LegalEntity.legal_name, SecurityListing.ticker)
            # Bounded in the QUERY. Loading the table and slicing afterwards has
            # already paid for the scan the cap exists to prevent.
            .limit(request.cap)
        )
        if self.exchange_codes:
            stmt = stmt.where(SecurityListing.exchange_code.in_(self.exchange_codes))

        for listing, entity in (await self.session.execute(stmt)).all():
            country = country_for_exchange(listing.exchange_code)
            out.candidates.append(
                UniverseCandidate(
                    ticker=listing.ticker,
                    exchange=listing.exchange_code or listing.venue_key,
                    company_name=entity.legal_name,
                    country=country,
                    region=region_for_country(country),
                    theme=None,
                    relevance_reason=(
                        "already in the entity master with a current listing; no "
                        "thesis relevance computed by this provider"
                    ),
                    universe_source=self.provider_id,
                    source_tier=self.source_tier,
                    # Sector and industry are not on the entity master, and this
                    # provider does not invent them.
                    metadata_not_sourced=True,
                )
            )
        return out


__all__ = [
    "CURATED_PROVIDER_ID",
    "CURATED_SOURCE_TIER",
    "ENTITY_MASTER_PROVIDER_ID",
    "ENTITY_MASTER_SOURCE_TIER",
    "CuratedRegistryUniverseProvider",
    "EntityMasterUniverseProvider",
]
