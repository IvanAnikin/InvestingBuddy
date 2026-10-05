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
    REJECT_NO_LISTING_EVIDENCE,
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
#: Web-search states in which live search ran (so recall must be corroborated).
_LIVE_SEARCH_STATES = ("ok", "web_search_degraded")


@dataclass
class CandidateRecord:
    """Everything the run knows about one screened issuer — the ``v319`` payload."""

    identity: IdentityOutcome
    results: list[cons.ConstraintResult]
    eligibility: cons.Eligibility
    screening: ScreeningResult | None
    provenance: dict[str, Any]
    registry_item: dict[str, Any] | None = None
    #: Open-web W6b: ``v3_web`` — search provenance + admission (A1–A4). ``None`` with the
    #: web flag off, so the persisted ``v319`` payload is byte-identical to V3.19.
    web: dict[str, Any] | None = None

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
        payload: dict[str, Any] = {
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
        if self.web is not None:
            payload["v3_web"] = self.web
        return payload


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
    #: Open-web W6b: the run-level web summary (state, counts, families, cost units) and
    #: the ``eligible_unverified(theme)`` leads shown as "also surfaced". ``None`` / empty
    #: with the flag off.
    web: dict[str, Any] | None = None
    also_surfaced: list[CandidateRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = self._base_dict()
        if self.web is not None:
            out["web"] = self.web
            out["also_surfaced"] = [c.to_dict() for c in self.also_surfaced][:40]
        return out

    def _base_dict(self) -> dict[str, Any]:
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
                    **({"discovery_mode": r.lead.discovery_mode,
                        "admission": (r.lead.web or {}).get("admission")}
                       if r.lead.web else {}),
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


_PRIMARY_DOCUMENT_TIERS = ("T1_primary_filing", "T1_primary_company_source",
                           "T2_regulator_or_gov")


async def growth_from_held_facts(session: Any, company_id: Any) -> list[cons.GrowthObservation]:
    """Revenue growth from the company's OWN primary documents the platform already read.

    Validated, active, group-scope ANNUAL revenue facts (``extracted_facts``) for two
    consecutive fiscal years in one currency and scale → a revenue-pair growth
    observation, sourced to the issuer's filing. Never raises; [] when there is no pair.
    """
    if not company_id:
        return []
    try:
        from sqlalchemy import select

        from app.models.extracted_document import ExtractedDocument, ExtractedFact
        from app.services.sources.company_documents import company_documents_clause
        from app.services.sources.fact_scope import SCOPE_TYPE_GROUP
        from app.services.sources.financial_period import (
            PERIOD_TYPE_ANNUAL,
            PERIOD_TYPE_SPLIT_YEAR,
            parse_period,
        )

        async with session.begin_nested():
            rows = (
                await session.execute(
                    select(ExtractedFact, ExtractedDocument.canonical_url,
                           ExtractedDocument.source_tier)
                    .join(ExtractedDocument,
                          ExtractedFact.extracted_document_id == ExtractedDocument.id)
                    .where(company_documents_clause(company_id),
                           ExtractedFact.label == "revenue",
                           ExtractedFact.is_active.is_(True),
                           ExtractedFact.validation_status == "validated",
                           ExtractedDocument.source_tier.in_(_PRIMARY_DOCUMENT_TIERS))
                    .order_by(ExtractedFact.created_at.desc())
                    .limit(200)
                )
            ).all()
    except Exception:  # noqa: BLE001 - a held company's facts are an optional source
        return []
    # Keyed by (document, period type, year). A pair is taken from ONE document — an
    # annual report prints the year and its comparative side by side — so two documents
    # about different issuers can never be spliced into one growth figure. An annual year
    # pairs only with an annual year, a split fiscal year ("2024/25") only with a split.
    by_doc: dict[Any, dict[tuple[str, int], tuple[float, str, str, str | None, str | None,
                                                  str]]] = {}
    for fact, url, tier in rows:
        if fact.scope_type != SCOPE_TYPE_GROUP or fact.value_numeric is None:
            continue
        period = parse_period(fact.period)
        if (period.period_type not in (PERIOD_TYPE_ANNUAL, PERIOD_TYPE_SPLIT_YEAR)
                or period.year is None or not fact.currency):
            continue
        # Newest extraction wins per period (rows are newest-first).
        by_doc.setdefault(fact.extracted_document_id, {}).setdefault(
            (period.period_type, period.year),
            (float(fact.value_numeric), fact.currency, fact.scale or "", url, tier,
             fact.period or ""))
    best: tuple[tuple, tuple] | None = None
    for by_year in by_doc.values():
        # The newest period in this document that HAS a same-type, same-currency,
        # same-scale predecessor.
        for latest in sorted(by_year, key=lambda key: key[1], reverse=True):
            prior = by_year.get((latest[0], latest[1] - 1))
            current = by_year[latest]
            if prior is not None and prior[1:3] == current[1:3]:
                if best is None or latest[1] > best[0][1]:
                    best = ((latest[0], latest[1]), (current, prior))
                break
    if best is None:
        return []
    current, prior = best[1]
    observation = cons.growth_from_revenue_pair(
        current[0], prior[0], period=current[5], base_period=prior[5],
        source_url=current[3], source_tier=current[4], verified=True,
    )
    return [observation] if observation is not None else []


async def _official_cap(identity: IdentityOutcome, *, cfg: Any, fetcher: Any):
    """The exchange's own market capitalisation for this listing, when it publishes one."""
    row = identity.directory_listing
    if not row:
        return None
    from app.services.discovery.directories import DirectoryListing, official_market_cap

    try:
        listing = DirectoryListing(**row)
        amount, currency, url, basis = await official_market_cap(listing, cfg=cfg,
                                                                 fetcher=fetcher)
    except Exception:  # noqa: BLE001 - an official figure that cannot be read is absent
        return None
    if (not amount or not currency) and listing.directory == "sec":
        from app.services.discovery.directories import sec_public_float

        floor = await sec_public_float(listing.cik, cfg=cfg, fetcher=fetcher)
        if floor is None:
            return None
        return cons.MarketCapObservation(
            amount=floor[0], currency="USD", as_of=floor[1], as_of_basis="stated",
            source_url=floor[2], source_tier=listing.tier, verified=True,
            method=cons.FLOAT_FLOOR_METHOD,
        )
    if not amount or not currency:
        return None
    return cons.MarketCapObservation(
        amount=amount, currency=currency, as_of=listing.as_of, as_of_basis="fetch_date",
        source_url=url, source_tier=listing.tier, verified=True,
        method="exchange_directory" if "published" in basis else "admitted_shares_x_price",
    )


async def evaluate(
    identity: IdentityOutcome,
    screening: ScreeningResult | None,
    intent: DiscoveryIntent,
    *,
    cfg: Any,
    fx_fetcher: Any = None,
    session: Any = None,
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
    observation = await _official_cap(identity, cfg=cfg, fetcher=fx_fetcher)
    screened_cap = screening.market_cap if screening else None
    # A lower bound yields to an actual verified figure; an official figure never does.
    if observation is None or (observation.method == cons.FLOAT_FLOOR_METHOD
                               and screened_cap is not None and screened_cap.verified):
        observation = screened_cap or observation
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
    growth = list(screening.growth) if screening else []
    if session is not None and identity.company_id:
        growth.extend(await growth_from_held_facts(session, identity.company_id))
    results.append(cons.verify_growth(intent.growth, growth))
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
    run_id: Any = None,
    web_deps: Any = None,
    progress: Any = None,
    commit: Any = None,
    plan_date: Any = None,
    expansion_loader: Any = None,
    expansion_saver: Any = None,
) -> StageResult:
    """The whole stage. Never raises for a single lead's failure.

    Open-web W6b: with ``V3_DISCOVERY_WEB_SEARCH_ENABLED`` on (and ``external``), wave 1 of
    the open-web flow adds a ``discovery_mode="search"`` lead source AFTER the curated and
    held leads and BEFORE model recall, and admission rules A1–A4 gate those leads. With
    the flag off every line below behaves exactly as in V3.19.
    """
    started = time.monotonic()
    stage = StageResult()
    if provider is None and external:
        from app.services.agents.routing import research_provider_for

        provider = research_provider_for(cfg)

    # 1. Leads, in priority order: curated registry, held companies, web search (W6b),
    #    then model recall.
    held = await _held_companies(session)
    registry = _registry_leads(run_universe)
    leads: list[CompanyLead] = [*registry, *_held_leads(held, intent)]
    web_result: Any = None
    web_leads: list[CompanyLead] = []
    if external and _web_enabled(cfg):
        from app.services.web_research.discovery_stage import (
            DiscoveryWebContext,
            run_discovery_web_stage,
        )

        known_domains, known_keys = _known_names(leads, held)
        web_result = await run_discovery_web_stage(
            session, intent,
            DiscoveryWebContext(run_id=run_id, known_domains=known_domains,
                                known_keys=known_keys, commit=commit, plan_date=plan_date,
                                expansion_loader=expansion_loader,
                                expansion_saver=expansion_saver),
            cfg=cfg, deps=web_deps, progress=progress,
        )
        stage.web = dict(web_result.summary)
        web_leads = list(web_result.leads)
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
    if web_result is not None and web_result.ran:
        units.append(web_result.units)
    recall_live = web_result is not None and web_result.state in _LIVE_SEARCH_STATES
    recall_corroborated: dict[str, dict[str, Any]] = {}
    recall_executed: frozenset[str] = frozenset()
    if recall_live:
        # Owner rule: with live search available, a model-named company is a FINAL candidate
        # only if an executed search + a fetched page also surface it (A1), its listing is
        # verified (A2) and a fetched passage ties it to the theme (A3). Targeted, budgeted
        # verification searches look for the recalled names no search found by itself.
        from app.services.web_research.discovery_stage import (
            DiscoveryWebContext,
            corroborate_recall_leads,
        )

        known_names = {normalised_name(x.name) for x in [*leads, *web_leads]}
        known_keys_now = {
            dedup_key(x.ticker, normalise_venue(x.exchange_raw) or x.exchange_raw)
            for x in [*leads, *web_leads]
        }
        to_check = [
            x for x in external_leads
            if x.discovery_mode == "model_recall"
            and normalised_name(x.name) not in known_names
            and dedup_key(x.ticker, normalise_venue(x.exchange_raw) or x.exchange_raw)
            not in known_keys_now
        ]
        found_by = await corroborate_recall_leads(
            session, intent, to_check,
            DiscoveryWebContext(run_id=run_id, known_domains=known_domains, commit=commit,
                                plan_date=plan_date),
            cfg=cfg, deps=web_deps, progress=progress,
        )
        recall_corroborated = found_by.by_lead
        recall_executed = found_by.executed_query_ids
        units.append(found_by.units)
        stage.web = {**(stage.web or {}), "recall_verification": {
            "attempted": found_by.attempted, "corroborated": len(found_by.by_lead),
            "notes": found_by.notes[:4]}}
        for x in to_check:
            if x.lead_id in recall_corroborated:
                x.web = recall_corroborated[x.lead_id]
    raw_count = len(leads) + len(web_leads) + len(external_leads)

    # 2. Dedup — cheap, before any fetch. A lead matching a HELD company inherits its
    #    identity (reference data on record) and is not re-verified.
    held_by_key = {dedup_key(c.ticker, c.exchange): c for c in held}
    seen_keys: set[str] = set()
    seen_names: set[str] = set()
    first_by_key: dict[str, CompanyLead] = {}
    first_by_name: dict[str, CompanyLead] = {}
    #: dedup key / normalised name -> the search provenance that corroborates an EARLIER
    #: (registry / held) lead. The lead keeps its true label; the passages are context.
    corroboration: dict[str, dict[str, Any]] = {}
    web_on = web_result is not None
    to_verify: list[CompanyLead] = []
    identities: list[IdentityOutcome] = []
    executed_query_ids = (
        (web_result.executed_query_ids if web_result is not None else frozenset())
        | recall_executed
    )
    for lead in [*leads, *web_leads, *external_leads]:
        venue = normalise_venue(lead.exchange_raw) or (lead.exchange_raw or None)
        key = dedup_key(lead.ticker, venue)
        name_key = normalised_name(lead.name)
        if lead.discovery_mode == "search":
            # A1, BEFORE any verification is spent: a lead labelled ``search`` must show an
            # EXECUTED query row and a FETCHED page. The label is a claim about the
            # network, and the network is checked.
            from app.services.discovery import admission as adm

            if not adm.search_lead_has_provenance(lead.web, executed_query_ids):
                a1_decision = adm.decide(discovery_mode="search", has_provenance=False,
                                         identity_verified=False)
                _attach_admission(lead, a1_decision.to_dict())
                stage.rejected.append(
                    IdentityOutcome(lead=lead, status=IDENTITY_REJECTED, ticker=lead.ticker,
                                    exchange=venue, name=lead.name,
                                    rejection_reason=adm.CODE_NO_SEARCH_PROVENANCE,
                                    detail=a1_decision.detail or "")
                )
                continue
        if (key and key in seen_keys) or (name_key and name_key in seen_names):
            earlier = (first_by_key.get(key) if key else None) or first_by_name.get(name_key)
            if (lead.discovery_mode == "model_recall" and earlier is not None
                    and earlier.discovery_mode == "search"):
                # A search found it independently, so the search lead stands (a truer label
                # than the model's); the recall is recorded on it, not rejected as a duplicate.
                earlier.web = {**(earlier.web or {}), "also_named_by_recall": True}
                continue
            if lead.discovery_mode == "search" and earlier is not None and lead.web:
                # The search surfaced a company a registry / held / earlier lead already
                # names. That lead keeps its true label; the search provenance is attached
                # as corroboration (and its passages are A3 context), not as a rejection.
                # Bound to the LISTING: a page about a different venue:ticker that happens to
                # share the normalised name is not corroboration of this company.
                earlier_key = dedup_key(
                    earlier.ticker, normalise_venue(earlier.exchange_raw) or earlier.exchange_raw
                )
                if key is None or earlier_key is None or key == earlier_key:
                    for ident in (key, name_key):
                        if ident:
                            corroboration.setdefault(ident, lead.web)
                continue
            stage.rejected.append(
                IdentityOutcome(lead=lead, status=IDENTITY_REJECTED, ticker=lead.ticker,
                                exchange=venue, name=lead.name,
                                rejection_reason=REJECT_DUPLICATE,
                                detail="the same company is already in this run")
            )
            continue
        if key:
            seen_keys.add(key)
            first_by_key[key] = lead
        if name_key:
            seen_names.add(name_key)
            first_by_name[name_key] = lead
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
            return await verify_identity(lead, cfg=cfg, fetcher=fetcher,
                                         directory_fetcher=fetcher)

    to_verify = _verification_order(to_verify, limit)
    verified_external = await asyncio.gather(*(_verify(lead) for lead in to_verify[:limit]))

    # V3.19.10 — curated and held companies are looked up in their exchange's own
    # directory too: it confirms the listing and carries the official market data.
    from app.services.discovery.directories import find_listing

    async def _enrich(identity: IdentityOutcome) -> None:
        async with gate:
            row, _reason = await find_listing(
                name=identity.name, ticker=identity.ticker, venue=identity.exchange,
                cfg=cfg, fetcher=fetcher,
            )
        if row is not None:
            identity.directory_listing = row.to_dict()

    await asyncio.gather(*(_enrich(i) for i in identities))
    # A curated identity for a company the platform already holds carries its id, so
    # the company's own extracted facts can serve as evidence.
    for identity in identities:
        if identity.company_id is None:
            held_row = held_by_key.get(dedup_key(identity.ticker, identity.exchange))
            if held_row is not None:
                identity.company_id = str(held_row.id)
    for lead in to_verify[limit:]:
        stage.rejected.append(
            IdentityOutcome(lead=lead, status=IDENTITY_REJECTED, ticker=lead.ticker,
                            name=lead.name, rejection_reason=REJECT_BUDGET,
                            detail=f"beyond the {limit}-verification bound")
        )
    unverifiable: list[IdentityOutcome] = []
    for outcome in verified_external:
        if web_on and outcome.lead.discovery_mode == "search":
            from app.services.discovery import admission as adm

            outcome = _strict_name_guard(outcome)
            if not outcome.verified and outcome.rejection_reason == REJECT_NO_LISTING_EVIDENCE:
                # No directory covers the venue and no OFFICIAL page confirmed the listing:
                # eligible_unverified(identity). Shown, never admitted, never rejected.
                _attach_admission(
                    outcome.lead,
                    adm.decide_unverifiable_venue(
                        _a3_mentions(outcome.lead, outcome.name)).to_dict(),
                )
                unverifiable.append(outcome)
                continue
            decision = adm.decide(
                discovery_mode="search", has_provenance=True,
                identity_verified=outcome.verified,
                identity_reason=outcome.rejection_reason,
                mentions=_a3_mentions(outcome.lead, outcome.name),
            )
            _attach_admission(outcome.lead, decision.to_dict())
        elif recall_live and outcome.lead.discovery_mode == "model_recall":
            from app.services.discovery import admission as adm

            recall_decision = adm.decide_recall(
                has_provenance=adm.search_lead_has_provenance(outcome.lead.web,
                                                              executed_query_ids),
                identity_verified=outcome.verified,
                identity_reason=outcome.rejection_reason,
                mentions=_a3_mentions(outcome.lead, outcome.name),
                surfaced_by=((outcome.lead.web or {}).get("surfaced_by") or []),
            )
            _attach_admission(outcome.lead, recall_decision.to_dict())
        (identities if outcome.verified else stage.rejected).append(outcome)
    if web_on:
        # Leads beyond the verification bound were never verified: record why.
        for lead in to_verify[limit:]:
            if lead.discovery_mode == "search":
                _attach_admission(lead, {"state": "rejected", "codes": [REJECT_BUDGET]})

    # 4. Cheap geography pre-filter: a verified listing clearly outside the requested
    #    geography (and a claimed country outside it too) is excluded without screening.
    to_screen: list[IdentityOutcome] = []
    records: list[CandidateRecord] = []
    also_surfaced: list[CandidateRecord] = []
    for identity in identities:
        if web_on and _admission_state(identity) == "also_surfaced":
            # A3 missing: ``eligible_unverified(theme)``. No screening is spent on it, and
            # it can never fill the quota; a hard-constraint failure still excludes it.
            results, eligibility = await evaluate(identity, None, intent, cfg=cfg,
                                                  fx_fetcher=fetcher)
            record = _make_record(identity, results, eligibility, None, corroboration, web_on)
            (records if eligibility.status == cons.EXCLUDED else also_surfaced).append(record)
            continue
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
            records.append(_make_record(identity, results, eligibility, None, corroboration,
                                        web_on))
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
                                              fx_fetcher=fetcher, session=session)
        records.append(_make_record(identity, results, eligibility, screening, corroboration,
                                    web_on))

    for outcome in unverifiable:
        also_surfaced.append(_unverifiable_record(outcome, corroboration))

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
    if web_on:
        stage.also_surfaced = also_surfaced
        stage.funnel.update(
            web_leads=len(web_leads),
            web_admitted=sum(1 for r in records if _record_state(r) == "admitted"),
            web_also_surfaced=len(also_surfaced),
        )
        stage.web = {**(stage.web or {}),
                     "admission": _admission_summary(records, also_surfaced, stage.rejected)}
    if units:
        from app.services.consumption import ConsumptionUnits

        total = ConsumptionUnits()
        for u in units:
            total = total + u
        stage.consumption = total
    stage.elapsed_seconds = time.monotonic() - started
    return stage


# --------------------------------------------------------------------------- #
# Open-web W6b helpers
# --------------------------------------------------------------------------- #


def _web_enabled(cfg: Any) -> bool:
    return bool(getattr(cfg, "v3_discovery_web_search_enabled", False))


def _known_names(
    leads: list[CompanyLead], held: list[Any]
) -> tuple[tuple[str, ...], frozenset[str]]:
    """IR / site domains and ``venue:ticker`` keys of the names this run already knows.

    Saturation (spec §5.3) and the novelty metric compare against these: a curated-registry
    or held name's own site is "known", and a candidate found elsewhere is "novel".
    """
    from app.services.sources.verified_issuer_sources import get_verified_issuer_source

    domains: list[str] = []
    keys: set[str] = set()
    pairs = [(lead.ticker, lead.exchange_raw) for lead in leads]
    pairs += [(c.ticker, c.exchange) for c in held]
    for ticker, exchange in pairs:
        venue = normalise_venue(exchange) or exchange
        key = dedup_key(ticker, venue)
        if key:
            keys.add(key)
        verified = get_verified_issuer_source(ticker, exchange)
        if verified is not None:
            for d in (verified.official_website_domain, *verified.allowed_domains,
                      *verified.document_domains):
                if d:
                    domains.append(d.lower().removeprefix("www."))
    return tuple(dict.fromkeys(domains)), frozenset(keys)


def _unverifiable_record(
    outcome: IdentityOutcome, corroboration: dict[str, dict[str, Any]]
) -> CandidateRecord:
    """``eligible_unverified(identity)``: shown in "also surfaced", never a candidate."""
    eligibility = cons.Eligibility(
        cons.ELIGIBLE_UNVERIFIED,
        ["identity: the listing is not confirmed by an official source for this venue"],
        0, 0, ["listing"], [],
    )
    results = [cons.verify_listing(outcome.identity_record())]
    web = dict(outcome.lead.web or {})
    web["mentions"] = _a3_mentions(outcome.lead, outcome.name)
    return CandidateRecord(
        outcome, results, eligibility, None, _provenance(outcome), None, web=web
    )


def _verification_order(leads: list[CompanyLead], limit: int) -> list[CompanyLead]:
    """Web leads first, but never ALL the slots: at least a quarter of the verification bound
    is kept for recalled leads (a flood of web leads must not starve them into
    ``verification_budget_exhausted``). Order within each group is the discovery order."""
    other = [x for x in leads if x.discovery_mode != "search"]
    web = [x for x in leads if x.discovery_mode == "search"]
    reserve = min(len(other), max(1, limit // 4)) if other else 0
    chosen_web = web[: max(0, limit - reserve)]
    # the chosen web leads, then the reserved (recalled) ones, then any leftover web leads
    return [*chosen_web, *other, *web[len(chosen_web) :]]


def _strict_name_guard(outcome: IdentityOutcome) -> IdentityOutcome:
    """A name collision on a real ticker, caught for WEB leads.

    The directory lookup accepts a listing when the ticker matched and the names share one
    distinctive word (an exchange abbreviates: "AIR LIQUIDE" for "L'Air Liquide"). That is
    right for a lead a model recalled; it is too lenient for a name read off a web page
    (the page's "Apex Metals" must not become the directory's "Apex Fisheries"). For a
    search lead verified through a directory the printed name must also agree under the
    STRICT rule — equal, or one the other plus generic trailing words.
    """
    listing = outcome.directory_listing
    if not outcome.verified or not listing:
        return outcome
    from app.services.discovery.directories import _expand, _names_agree

    ours, theirs = normalised_name(outcome.lead.name), normalised_name(str(listing.get("name")))
    if _names_agree(_expand(ours), _expand(theirs), ticker_matched=False):
        return outcome
    # A headline puts words before the name ("Rare Earth Miner Lynas (ASX: LYC)"): try the
    # TRAILING sub-names, longest first. The match is still strict on the sub-name itself,
    # so a different company cannot pass by sharing a word.
    words = ours.split()
    for size in range(len(words) - 1, 0, -1):
        sub = " ".join(words[-size:])
        if len(sub) >= 4 and _names_agree(_expand(sub), _expand(theirs), ticker_matched=False):
            outcome.lead.name = str(listing.get("name") or sub)
            return outcome
    return IdentityOutcome(
        lead=outcome.lead, status=IDENTITY_REJECTED, ticker=outcome.ticker,
        exchange=outcome.exchange, name=outcome.name,
        rejection_reason="name_mismatch_with_listing",
        detail=f"the page's name does not match the exchange's name for {outcome.ticker}",
    )


def _attach_admission(lead: CompanyLead, decision: dict[str, Any]) -> None:
    lead.web = {**(lead.web or {}), "admission": decision}


def _a3_mentions(lead: CompanyLead, name: str | None) -> list[dict[str, Any]]:
    """The lead's mention passages, with a REGISTRY-verified issuer's own pages counted as
    issuer material.

    The classifier cannot know an unfamiliar issuer's domain, so a page on it reads as
    ``unknown_web`` and cannot carry A3. Only the platform's own verified-issuer registry
    (``verified_issuer_sources``) may say a domain IS the issuer's; a domain that merely
    looks like the company's name (``fakeco.se``) never is — anyone can register one.
    """
    from urllib.parse import urlsplit

    from app.services.sources.verified_issuer_sources import get_verified_issuer_source

    verified = get_verified_issuer_source(
        lead.ticker, normalise_venue(lead.exchange_raw) or lead.exchange_raw
    )
    hosts: tuple[str, ...] = ()
    if verified is not None:
        hosts = tuple(
            d.lower().removeprefix("www.")
            for d in (verified.official_website_domain, *verified.allowed_domains)
            if d
        )
    out: list[dict[str, Any]] = []
    for mention in (lead.web or {}).get("mentions") or []:
        entry = dict(mention)
        host = (urlsplit(entry.get("url") or "").hostname or "").lower().removeprefix("www.")
        if hosts and any(host == h or host.endswith("." + h) for h in hosts):
            entry["source_class"] = "company_web_page"
        out.append(entry)
    return out


def _admission_state(identity: IdentityOutcome) -> str | None:
    return ((identity.lead.web or {}).get("admission") or {}).get("state")


def _web_block(
    identity: IdentityOutcome,
    eligibility: cons.Eligibility,
    corroboration: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    from app.services.discovery import admission as adm

    lead = identity.lead
    has_admission = bool(((lead.web or {}).get("admission") or {}).get("state"))
    if lead.web and (lead.discovery_mode == "search" or has_admission):
        found = dict(lead.web)
        found.setdefault("discovery_mode", lead.discovery_mode)
        # Evidence ids use the passages as A3 saw them (issuer pages upgraded).
        found["mentions"] = _a3_mentions(lead, identity.name)
        found["admission"] = adm.apply_a4(
            dict(found.get("admission") or {}), status=eligibility.status,
            reasons=eligibility.reasons,
        )
        return found
    corr = (
        corroboration.get(dedup_key(identity.ticker, identity.exchange) or "")
        or corroboration.get(normalised_name(identity.name))
        or lead.web
    )
    labelled = adm.decide(
        discovery_mode=lead.discovery_mode,
        has_provenance=False,
        identity_verified=True,
        mentions=_a3_mentions(
            CompanyLead(name=identity.name or lead.name, ticker=lead.ticker,
                        exchange_raw=lead.exchange_raw, country=None, listing_source_url=None,
                        evidence_url=None, why=None, source=lead.source, web=corr),
            identity.name,
        ) if corr else [],
        lead_source=lead.source,
    )
    block: dict[str, Any] = {
        "schema": "discovery_web_lead/1",
        "discovery_mode": lead.discovery_mode or lead.source,
        "admission": adm.apply_a4(labelled.to_dict(), status=eligibility.status,
                                  reasons=eligibility.reasons),
    }
    if corr:
        block["corroborated_by_search"] = {
            "sightings": (corr.get("sightings") or [])[:4],
            "mentions": _a3_mentions(
                CompanyLead(name=identity.name or lead.name, ticker=lead.ticker,
                            exchange_raw=lead.exchange_raw, country=None,
                            listing_source_url=None, evidence_url=None, why=None,
                            source=lead.source, web=corr),
                identity.name,
            )[:6],
        }
    return block


def _make_record(
    identity: IdentityOutcome,
    results: list[cons.ConstraintResult],
    eligibility: cons.Eligibility,
    screening: ScreeningResult | None,
    corroboration: dict[str, dict[str, Any]],
    web_on: bool,
) -> CandidateRecord:
    return CandidateRecord(
        identity, results, eligibility, screening, _provenance(identity),
        identity.lead.registry_item,
        web=_web_block(identity, eligibility, corroboration) if web_on else None,
    )


def _record_state(record: CandidateRecord) -> str | None:
    return ((record.web or {}).get("admission") or {}).get("state")


def _admission_summary(
    records: list[CandidateRecord], also: list[CandidateRecord], rejected: list[IdentityOutcome]
) -> dict[str, Any]:
    states: dict[str, int] = {}
    for r in [*records, *also]:
        state = _record_state(r) or "unknown"
        states[state] = states.get(state, 0) + 1
    codes: dict[str, int] = {}
    for o in rejected:
        admission = (o.lead.web or {}).get("admission")
        if admission:
            for code in admission.get("codes") or []:
                codes[str(code)] = codes.get(str(code), 0) + 1
    for r in also:
        for code in ((r.web or {}).get("admission") or {}).get("codes") or []:
            codes[f"also_surfaced:{code}"] = codes.get(f"also_surfaced:{code}", 0) + 1
    novel = sum(
        1 for r in records
        if r.identity.lead.discovery_mode == "search" and (r.web or {}).get("novel")
        and r.eligibility.status != cons.EXCLUDED
    )
    return {"by_state": states, "rejected_codes": dict(sorted(codes.items())),
            "novel_candidates": novel}


def _provenance(identity: IdentityOutcome) -> dict[str, Any]:
    lead = identity.lead
    return {
        "discovery_source": lead.source,
        "discovery_mode": lead.discovery_mode,
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
