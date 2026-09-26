"""The dynamic discovery stage: leads → verified issuers → screening → eligibility. V3.19.4.

Runs once per intent-driven thesis run, BEFORE the per-ticker signal scan, and replaces the
run's universe with the eligible, ranked, bounded shortlist. Everything it looked at —
every lead, every rejection with its reason, every excluded company with the constraint it
failed — is persisted on the run (``universe_json["dynamic"]``): rejected companies are
learning data (CLAUDE.md rule 8), and "why is this company here?" must be answerable for
every candidate.

Sources of leads, all treated alike downstream:
* the curated registry (T3 reference data already on record — ``platform_registry``);
* companies the platform already holds whose classification matches the intent;
* external discovery (``discovery.leads``), verified by ``discovery.identity``.

Nothing here writes a ``companies`` row. Only the returned candidates reach
``ensure_company``, in the existing scan (spec §5.3 lifecycle).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from app.services.discovery import constraints as cons
from app.services.discovery.fx import usd_rate
from app.services.discovery.identity import (
    IDENTITY_REJECTED,
    REJECT_BUDGET,
    REJECT_DUPLICATE,
    IdentityOutcome,
    dedup_key,
    normalise_venue,
    normalised_name,
    platform_identity,
    verify_identity,
)
from app.services.discovery.intent import DiscoveryIntent
from app.services.discovery.leads import CompanyLead, _bounded, discover_leads
from app.services.discovery.screening import ScreeningResult, screen_issuers

STAGE_KEY = "dynamic"
HARD_MAX_VERIFIED = 40
DEFAULT_MAX_VERIFIED = 30
DEFAULT_MAX_CANDIDATES = 10
_HELD_COMPANY_SCAN = 500


@dataclass
class CandidateRecord:
    """Everything the run knows about one screened issuer — the ``v319`` payload."""

    identity: IdentityOutcome
    results: list[cons.ConstraintResult]
    eligibility: cons.Eligibility
    screening: ScreeningResult | None
    provenance: dict[str, Any]
    registry_item: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        verified = cons.verified_attributes(self.results)
        provenance = dict(self.provenance)
        # The lead's own reason was written against the user's brief ("a fast-growing
        # small-cap jeweller"); it is shown only if it attributes nothing unverified.
        why = provenance.get("why")
        if why:
            from app.services.discovery.attribute_guard import (
                CandidateAttributes,
                check_sentence,
            )

            own = CandidateAttributes([{"ticker": self.identity.ticker,
                                        "verified_attributes": verified}])
            reason = check_sentence(str(why), own, own.all[0])
            if reason:
                provenance["why"] = None
                provenance["why_withheld"] = reason
        return {
            "schema": "discovery_candidate/1",
            "identity": self.identity.identity_record(),
            "provenance": provenance,
            "constraint_results": [r.to_dict() for r in self.results],
            "verified_attributes": verified,
            "unknown_constraints": [
                r.key for r in self.results if r.status == cons.UNKNOWN
            ],
            "failed_constraints": [r.key for r in self.results if r.status == cons.FAIL],
            "eligibility": self.eligibility.to_dict(),
            "screening": self.screening.to_dict() if self.screening else {"status": "not_screened"},
        }


@dataclass
class StageResult:
    candidates: list[CandidateRecord] = field(default_factory=list)  # returned, ranked
    excluded: list[CandidateRecord] = field(default_factory=list)
    rejected: list[IdentityOutcome] = field(default_factory=list)
    queries: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    funnel: dict[str, int] = field(default_factory=dict)
    consumption: Any = None
    external_available: bool = False
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "discovery_dynamic_stage/1",
            "status": "completed",
            "external_discovery": "available" if self.external_available else "unavailable",
            "funnel": dict(self.funnel),
            "queries": self.queries,
            "excluded": [c.to_dict() for c in self.excluded],
            "rejected_leads": [
                {
                    "name": _safe_text(r.lead.name, 200),
                    "ticker": r.lead.ticker,
                    "exchange_raw": r.lead.exchange_raw,
                    "exchange": r.exchange,
                    "source": r.lead.source,
                    "listing_source_url": r.lead.listing_source_url,
                    "why": _safe_text(r.lead.why, 200),
                    "rejection_reason": r.rejection_reason,
                    "detail": r.detail,
                }
                for r in self.rejected
            ][:120],
            "warnings": self.warnings[:40],
            "elapsed_seconds": round(self.elapsed_seconds, 1),
        }


def _registry_leads(run_universe: dict[str, Any] | None) -> list[CompanyLead]:
    items = ((run_universe or {}).get("items")) or []
    leads = []
    for item in items:
        if not item.get("ticker"):
            continue
        leads.append(
            CompanyLead(
                name=str(item.get("company_name") or item.get("ticker")),
                ticker=str(item.get("ticker")),
                exchange_raw=str(item.get("exchange") or ""),
                country=item.get("country"),
                listing_source_url=None,
                evidence_url=None,
                why=item.get("relevance_reason"),
                source="curated_registry",
                registry_item=dict(item),
            )
        )
    return leads


async def _held_companies(session: Any) -> list[Any]:
    from sqlalchemy import select

    from app.models.company import Company

    try:
        async with session.begin_nested():
            rows = (await session.execute(select(Company).limit(_HELD_COMPANY_SCAN))).scalars()
            return list(rows)
    except Exception:  # noqa: BLE001 - the held registry is an optional source
        return []


def _held_leads(held: list[Any], intent: DiscoveryIntent) -> list[CompanyLead]:
    """Companies the platform already holds whose CLASSIFICATION matches the intent.

    The theme recorded is the one whose OWN industry list names the company's industry —
    never simply the intent's first theme — so a held company's industry PASS rests on its
    stored classification, not on a label this function wrote.
    """
    from app.services.market_thesis_parser import _THEME_TABLE
    from app.services.sector_taxonomy import normalize_industry

    if not intent.themes:
        return []
    out = []
    for company in held:
        industry = getattr(company, "industry", None)
        canonical = normalize_industry(industry) or industry
        theme = next(
            (t for t in intent.themes
             if canonical and canonical in (_THEME_TABLE.get(t, {}).get("industries") or [])),
            None,
        )
        if theme is None:
            continue
        out.append(
            CompanyLead(
                name=str(company.name or company.ticker),
                ticker=company.ticker,
                exchange_raw=company.exchange,
                country=getattr(company, "country", None),
                listing_source_url=None,
                evidence_url=None,
                why=f"held company classified {industry}",
                source="platform_registry",
                registry_item={
                    "ticker": company.ticker, "exchange": company.exchange,
                    "company_name": company.name, "industry": industry,
                    "sector": getattr(company, "sector", None),
                    "country": getattr(company, "country", None),
                    "theme": theme,
                    "universe_source": "platform_company_registry",
                    "source_tier": "T3_curated_reference_list",
                },
            )
        )
    return out[:20]


def _registry_match(lead: CompanyLead, intent: DiscoveryIntent) -> dict[str, Any] | None:
    """A curated/held classification naming a requested theme — T3 reference data."""
    item = lead.registry_item or {}
    theme = item.get("theme")
    if lead.source in ("curated_registry", "platform_registry") and theme in intent.themes:
        return {"theme": theme, "industry": item.get("industry"),
                "source": item.get("universe_source") or lead.source,
                "tier": item.get("source_tier") or "T3_curated_reference_list"}
    return None


async def evaluate(
    identity: IdentityOutcome,
    screening: ScreeningResult | None,
    intent: DiscoveryIntent,
    *,
    cfg: Any,
    fx_fetcher: Any = None,
) -> tuple[list[cons.ConstraintResult], cons.Eligibility]:
    """Constraint results + eligibility for one issuer. Deterministic given its inputs."""
    results = [cons.verify_listing(identity.identity_record())]
    listing_source = identity.listing_source
    results.append(
        cons.verify_geography(
            intent.geography,
            listing_country=identity.listing_country,
            listing_source=listing_source,
            hq_country=screening.hq_country if screening else None,
            hq_source=screening.hq_source if screening else None,
        )
    )
    results.append(
        cons.verify_industry(
            intent.industry,
            screening.exposures if screening else [],
            registry_match=_registry_match(identity.lead, intent),
        )
    )
    observation = screening.market_cap if screening else None
    rate = None
    reason = None
    if observation is not None and observation.verified:
        from datetime import date

        # Only a date the PAGE states dates the rate. A fetch-date observation uses the
        # latest published rate: H.10 lags about a week, and "today" would find none.
        as_of = None
        if observation.as_of_basis == "stated" and observation.as_of:
            try:
                as_of = date.fromisoformat(str(observation.as_of)[:10])
            except ValueError:
                as_of = None
        rate, reason = await usd_rate(observation.currency, as_of, cfg=cfg, fetcher=fx_fetcher)
    results.append(cons.verify_size(intent.size, observation, rate, fx_unavailable_reason=reason))
    results.append(cons.verify_growth(intent.growth, screening.growth if screening else []))
    if intent.profitability is not None:
        results.append(
            cons.ConstraintResult(
                "profitability", list(intent.profitability.requested),
                intent.profitability.hardness, cons.UNKNOWN,
                basis="profitability is not verified by the screening pass",
            )
        )
    return results, cons.decide_eligibility(results)


async def run_dynamic_stage(
    session: Any,
    *,
    intent: DiscoveryIntent,
    run_universe: dict[str, Any] | None,
    cfg: Any,
    provider: Any = None,
    fetcher: Any = None,
    max_candidates: int | None = None,
    external: bool = True,
) -> StageResult:
    """The whole stage. Never raises for a single lead's failure."""
    started = time.monotonic()
    stage = StageResult()
    if provider is None and external:
        from app.services.agents.routing import research_provider_for

        provider = research_provider_for(cfg)

    # 1. Leads, in priority order: curated registry, held companies, external search.
    held = await _held_companies(session)
    registry = _registry_leads(run_universe)
    leads: list[CompanyLead] = [*registry, *_held_leads(held, intent)]
    external_leads: list[CompanyLead] = []
    if external:
        found = await discover_leads(intent, cfg=cfg, provider=provider)
        stage.external_available = found.available
        stage.queries = found.queries
        stage.warnings.extend(found.warnings)
        external_leads = found.leads
        units = list(found.consumption)
    else:
        units = []
    raw_count = len(leads) + len(external_leads)

    # 2. Dedup — cheap, before any fetch. A lead matching a HELD company inherits its
    #    identity (reference data on record) and is not re-verified.
    held_by_key = {dedup_key(c.ticker, c.exchange): c for c in held}
    seen_keys: set[str] = set()
    seen_names: set[str] = set()
    to_verify: list[CompanyLead] = []
    identities: list[IdentityOutcome] = []
    for lead in [*leads, *external_leads]:
        venue = normalise_venue(lead.exchange_raw) or (lead.exchange_raw or None)
        key = dedup_key(lead.ticker, venue)
        name_key = normalised_name(lead.name)
        if (key and key in seen_keys) or (name_key and name_key in seen_names):
            stage.rejected.append(
                IdentityOutcome(lead=lead, status=IDENTITY_REJECTED, ticker=lead.ticker,
                                exchange=venue, name=lead.name,
                                rejection_reason=REJECT_DUPLICATE,
                                detail="the same company is already in this run")
            )
            continue
        if key:
            seen_keys.add(key)
        if name_key:
            seen_names.add(name_key)
        # A HELD company is inherited only on the same listing (venue + ticker). A name
        # match alone is not an identity: the lead is verified like any other.
        held_match = held_by_key.get(key) if key else None
        if lead.source != "external_search" or held_match is not None:
            identities.append(
                platform_identity(
                    lead if held_match is None else CompanyLead(
                        name=held_match.name or lead.name, ticker=held_match.ticker,
                        exchange_raw=held_match.exchange, country=lead.country,
                        listing_source_url=lead.listing_source_url,
                        evidence_url=lead.evidence_url, why=lead.why,
                        # Provenance keeps where the lead CAME FROM; the identity status
                        # says it matched a held listing.
                        source=lead.source,
                        discovery_query=lead.discovery_query, provider=lead.provider,
                        registry_item=lead.registry_item,
                    ),
                    exchange=(held_match.exchange if held_match is not None else venue),
                    company_id=str(held_match.id) if held_match is not None else None,
                )
            )
        else:
            to_verify.append(lead)

    # 3. Verify external leads' listings, bounded.
    limit = _bounded(getattr(cfg, "v3_discovery_max_verified", None), DEFAULT_MAX_VERIFIED,
                     HARD_MAX_VERIFIED)
    gate = asyncio.Semaphore(4)

    async def _verify(lead: CompanyLead) -> IdentityOutcome:
        async with gate:
            return await verify_identity(lead, cfg=cfg, fetcher=fetcher)

    verified_external = await asyncio.gather(*(_verify(lead) for lead in to_verify[:limit]))
    for lead in to_verify[limit:]:
        stage.rejected.append(
            IdentityOutcome(lead=lead, status=IDENTITY_REJECTED, ticker=lead.ticker,
                            name=lead.name, rejection_reason=REJECT_BUDGET,
                            detail=f"beyond the {limit}-verification bound")
        )
    for outcome in verified_external:
        (identities if outcome.verified else stage.rejected).append(outcome)

    # 4. Cheap geography pre-filter: a verified listing clearly outside the requested
    #    geography (and a claimed country outside it too) is excluded without screening.
    to_screen: list[IdentityOutcome] = []
    records: list[CandidateRecord] = []
    for identity in identities:
        geo = cons.verify_geography(
            intent.geography, listing_country=identity.listing_country,
            listing_source=identity.listing_source,
        )
        claimed = cons.verify_geography(
            intent.geography, listing_country=identity.lead.country,
            listing_source={"url": None, "tier": "claimed"},
        ) if identity.lead.country else None
        if (
            intent.geography is not None
            and intent.geography.hardness == "hard"
            and geo.status == cons.FAIL
            and (claimed is None or claimed.status == cons.FAIL)
        ):
            results, eligibility = await evaluate(identity, None, intent, cfg=cfg,
                                                  fx_fetcher=fetcher)
            records.append(CandidateRecord(identity, results, eligibility, None,
                                           _provenance(identity), identity.lead.registry_item))
            continue
        to_screen.append(identity)

    # 5. Screen, bounded (curated and held first, then external in discovery order).
    screened: dict[str, ScreeningResult] = {}
    if external and provider is not None and to_screen:
        screened = await screen_issuers(to_screen, intent, cfg=cfg, provider=provider,
                                        fetcher=fetcher)
        for result in screened.values():
            units.extend(result.consumption)
    for identity in to_screen:
        screening = screened.get(f"{identity.exchange}:{identity.ticker}")
        results, eligibility = await evaluate(identity, screening, intent, cfg=cfg,
                                              fx_fetcher=fetcher)
        records.append(CandidateRecord(identity, results, eligibility, screening,
                                       _provenance(identity), identity.lead.registry_item))

    # 6. Decide. Eligibility is final; the quota is never filled with excluded names.
    cap = _bounded(max_candidates, DEFAULT_MAX_CANDIDATES, 50)
    included = [r for r in records if r.eligibility.status != cons.EXCLUDED]
    included.sort(key=lambda r: cons.ranking_key(r.eligibility))
    stage.candidates = included[:cap]
    stage.excluded = [r for r in records if r.eligibility.status == cons.EXCLUDED]
    stage.funnel = {
        "raw_leads": raw_count,
        "external_leads": len(external_leads),
        "registry_leads": len(leads),
        "verified_issuers": len(identities),
        "rejected_leads": len(stage.rejected),
        "screened": len(screened),
        "excluded": len(stage.excluded),
        "met_hard_constraints": sum(
            1 for r in records if r.eligibility.status in (cons.ELIGIBLE,
                                                           cons.INCLUDED_WITH_MISMATCH)
        ),
        "eligible_unverified": sum(
            1 for r in records if r.eligibility.status == cons.ELIGIBLE_UNVERIFIED
        ),
        "returned": len(stage.candidates),
    }
    if units:
        from app.services.consumption import ConsumptionUnits

        total = ConsumptionUnits()
        for u in units:
            total = total + u
        stage.consumption = total
    stage.elapsed_seconds = time.monotonic() - started
    return stage


def _provenance(identity: IdentityOutcome) -> dict[str, Any]:
    lead = identity.lead
    return {
        "discovery_source": lead.source,
        "discovery_query": lead.discovery_query,
        "source_url": lead.evidence_url or lead.listing_source_url,
        "why": _safe_text(lead.why, 300),
        "verified_identity_source": (identity.listing_source or {}).get("url")
        or (identity.listing_source or {}).get("registry"),
        "identity_status": identity.status,
        "provider": lead.provider,
    }


def _safe_text(text: str | None, limit: int) -> str | None:
    from app.services.discovery.screening import _safe

    return _safe(text, limit)


def universe_item(record: CandidateRecord, intent: DiscoveryIntent) -> dict[str, Any]:
    """A shortlist entry in the SAME shape as a curated universe item, plus ``v319``."""
    identity = record.identity
    item = dict(record.registry_item or {})
    industry_result = next((r for r in record.results if r.key == "industry"), None)
    item.update(
        {
            "ticker": identity.ticker,
            "exchange": identity.exchange,
            "company_name": identity.name,
            "country": identity.listing_country or item.get("country"),
            "region": identity.listing_region,
            "theme": item.get("theme") or (intent.themes[0] if intent.themes else None),
            "matched_keywords": list((industry_result.value or {}).get("matched") or [])
            if industry_result and industry_result.value else [],
            "relevance_reason": (industry_result.basis if industry_result else None)
            or identity.lead.why,
            "universe_source": item.get("universe_source")
            or ("external_discovery" if identity.lead.source == "external_search"
                else identity.lead.source),
            "source_tier": item.get("source_tier")
            or (identity.listing_source or {}).get("tier"),
            "v319": record.to_dict(),
        }
    )
    return item


__all__ = [
    "CandidateRecord",
    "STAGE_KEY",
    "StageResult",
    "evaluate",
    "run_dynamic_stage",
    "universe_item",
]
