"""Lightweight screening of a verified issuer — V3.19.4.

One bounded investigation per issuer answers only what eligibility needs: market cap,
business growth, what the company does, where it is based. It is NOT V3.18 deep research
and costs roughly one search call plus a handful of the platform's own fetches.

Every answer arrives as a provider CLAIM with a URL and is put through ``verify_lead`` —
the same gate the Investigator's external rung uses: the platform fetches the cited page
itself and locates the figure beside the claim's own words. Only verified claims become
observations; unverified ones are kept, labelled, and shown as what they are — alleged,
not established.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from app.services.discovery.constraints import (
    EXPOSURE_DENIED,
    EXPOSURE_DIRECT,
    EXPOSURE_INDIRECT,
    ExposureObservation,
    GrowthObservation,
    MarketCapObservation,
    growth_from_revenue_pair,
)
from app.services.discovery.identity import IdentityOutcome, publisher_kind
from app.services.discovery.intent import DiscoveryIntent

HARD_MAX_SCREENED = 25
DEFAULT_MAX_SCREENED = 16
HARD_MAX_CONCURRENCY = 6
DEFAULT_CONCURRENCY = 4
DEFAULT_VERIFICATIONS_PER_ISSUER = 5

_SCALE = {
    "thousand": 1e3, "k": 1e3, "million": 1e6, "mn": 1e6, "m": 1e6, "mm": 1e6,
    "billion": 1e9, "bn": 1e9, "b": 1e9, "trillion": 1e12, "tn": 1e12,
}


@dataclass
class ScreeningResult:
    ticker: str | None
    exchange: str | None
    status: str  # completed | partial | failed | unavailable
    market_cap: MarketCapObservation | None = None
    growth: list[GrowthObservation] = field(default_factory=list)
    exposures: list[ExposureObservation] = field(default_factory=list)
    hq_country: str | None = None
    hq_source: dict[str, Any] | None = None
    business_description: str | None = None
    catalysts: list[dict[str, Any]] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    consumption: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "market_cap": self.market_cap.to_dict() if self.market_cap else None,
            "growth": [g.to_dict() for g in self.growth],
            "exposures": [e.to_dict() for e in self.exposures],
            "hq_country": self.hq_country,
            "business_description": self.business_description,
            "catalysts": self.catalysts[:4],
            "claims": self.claims[:24],
            "warnings": self.warnings[:10],
        }


def screening_question(issuer: IdentityOutcome, intent: DiscoveryIntent) -> str:
    """The bounded question. Built from the verified identity and the intent's CLOSED
    vocabulary; the user's own text is never sent (spec §5.2)."""
    focus = [m.replace("_", " ") for m in intent.materials] or [
        t.replace("_", " ") for t in intent.themes
    ]
    return (
        f"Research the listed company {issuer.name} (ticker {issuer.ticker} on "
        f"{issuer.exchange}). Report each item below as a separate finding with the exact "
        "`metric` name given, citing the page that states it (prefer the company's own "
        "annual report, results release or investor-relations page, or the exchange):\n"
        "1. metric 'market capitalisation': its current market capitalisation, with "
        "currency and the date the page states it for. If no page states it, instead give "
        "metric 'shares outstanding' and metric 'share price' (with currency and date).\n"
        "2. metric 'revenue': revenue for its latest full fiscal year AND, as a second "
        "finding, for the prior fiscal year (same currency, each with its period).\n"
        "3. metric 'organic revenue growth' and/or 'reported revenue growth': the latest "
        "year-on-year growth the company itself reports, in %, with the period. For a "
        "company with no revenue, metric 'production growth' or 'capacity growth'.\n"
        "4. metric 'business description': ONE sentence, in the company's own words, of "
        "what it produces or sells"
        + (f" — say whether and how it relates to {', '.join(focus)}" if focus else "")
        + ".\n"
        "5. metric 'headquarters': the country of its headquarters.\n"
        "6. metric 'catalyst': at most two dated recent or scheduled events (permits, "
        "funding, offtake, production start, contracts, results)."
    )


_NUMBER_WITH_SCALE = re.compile(
    r"(?P<sign>[-−])?\s*(?P<num>\d[\d,.\u202f\u00a0 ]*\d|\d)\s*"
    r"(?P<scale>thousand|million|billion|trillion|mn|mm|bn|tn|k|m|b)?(?![a-z])",
    re.IGNORECASE,
)


def _first_number(text: str | None) -> tuple[float, str | None, bool] | None:
    """(value, scale word, negative) of the first number in ``text``, or None."""
    from app.services.sources.primary_fact_parser import _norm_number

    for match in _NUMBER_WITH_SCALE.finditer(text or ""):
        value = _norm_number(match.group("num"))
        if value is None:
            continue
        return value, (match.group("scale") or "").lower() or None, bool(match.group("sign"))
    return None


def _amount(value: str | None, unit: str | None) -> float | None:
    """A money amount with its scale applied: "EUR 27.39 billion" → 27_390_000_000."""
    found = _first_number(value)
    if found is None:
        return None
    number, scale_word, negative = found
    if scale_word is None and unit:
        match = re.search(r"(thousand|million|billion|trillion|mn|mm|bn|tn)", unit.lower())
        scale_word = match.group(1) if match else None
    scale = _SCALE.get(scale_word or "", 1.0)
    amount = number * scale
    return -amount if negative else amount


def _percent(value: str | None) -> float | None:
    """A growth percentage, signed. A hyphen in "year-on-year" is never a minus."""
    found = _first_number(value)
    if found is None:
        return None
    number, _scale, negative = found
    if negative:
        return -number
    text = str(value).lower()
    if number > 0 and re.search(
        r"\b(?:declin\w*|decreas\w*|down|fell|drop\w*|negative|minus)\b", text
    ):
        return -number
    return number


def _currency(claimed: str | None, value: str | None) -> str | None:
    if claimed and re.fullmatch(r"[A-Za-z]{3}", claimed.strip()):
        return claimed.strip().upper()
    text = f"{claimed or ''} {value or ''}"
    for symbol, code in (("€", "EUR"), ("£", "GBP"), ("A$", "AUD"), ("C$", "CAD"),
                         ("CHF", "CHF"), ("DKK", "DKK"), ("SEK", "SEK"), ("NOK", "NOK"),
                         ("US$", "USD"), ("$", "USD"), ("GBX", "GBX"), ("GBp", "GBX")):
        if symbol in text:
            return code
    match = re.search(r"\b(EUR|GBP|USD|AUD|CAD|CHF|DKK|SEK|NOK|JPY|HKD)\b", text.upper())
    return match.group(1) if match else None


def _as_of(lead: Any) -> tuple[str, str]:
    claimed = getattr(lead, "claimed_date", None) or getattr(lead, "claimed_period", None)
    if claimed:
        text = claimed.isoformat() if isinstance(claimed, date) else str(claimed)
        return text[:20], "stated"
    return datetime.now(timezone.utc).date().isoformat(), "fetch_date"


def _terms(intent: DiscoveryIntent) -> dict[str, re.Pattern[str]]:
    """term → pattern, for the themes and materials the intent asks about."""
    from app.services.macro.commodities import commodity_for
    from app.services.market_thesis_parser import _THEME_TABLE

    out: dict[str, re.Pattern[str]] = {}
    for material in intent.materials:
        commodity = commodity_for(material)
        if commodity is not None:
            out[material] = re.compile(r"\b(?:" + "|".join(commodity.patterns) + r")\b", re.I)
    for theme in intent.themes:
        phrases = _THEME_TABLE.get(theme, {}).get("phrases") or []
        if theme == "critical_materials":
            phrases = ["critical mineral", "critical minerals", "critical material",
                       "strategic mineral", "strategic metals"]
        if theme == "mining_materials":
            phrases = [*phrases, "mine", "mines", "mineral", "ore", "deposit", "refinery",
                       "concentrate", "exploration", "project"]
        if phrases:
            out[theme] = re.compile(
                r"\b(?:" + "|".join(re.escape(p) for p in phrases) + r")\b", re.I
            )
    return out


_INDIRECT = re.compile(
    r"\b(?:suppl\w+ to|customers? in|used (?:in|by)|serv\w+ the|for the)\b", re.I
)


def exposures_from_text(
    text: str, terms: dict[str, re.Pattern[str]], *, source_url: str | None,
    source_tier: str | None, verified: bool,
) -> list[ExposureObservation]:
    """Theme/material exposure a VERIFIED passage states — affirmatively, per clause."""
    from app.services.director.thesis import _CLAUSE_SPLIT_RE, _NEGATION_CUES, _WORD_TOKEN_RE

    found: dict[str, ExposureObservation] = {}
    for clause in _CLAUSE_SPLIT_RE.split(text or ""):
        for term, pattern in terms.items():
            match = pattern.search(clause)
            if match is None:
                continue
            before = _WORD_TOKEN_RE.findall(clause[: match.start()].lower())
            if any(token in _NEGATION_CUES for token in before):
                kind = EXPOSURE_DENIED
            elif _INDIRECT.search(clause[: match.start()]):
                kind = EXPOSURE_INDIRECT
            else:
                kind = EXPOSURE_DIRECT
            existing = found.get(term)
            # An affirmative mention outranks a denial elsewhere (V3.18.14's rule).
            if existing is None or existing.exposure == EXPOSURE_DENIED:
                found[term] = ExposureObservation(
                    term, kind, clause.strip()[:300], source_url, source_tier, verified
                )
    return list(found.values())


async def screen_issuer(
    issuer: IdentityOutcome,
    intent: DiscoveryIntent,
    *,
    cfg: Any,
    provider: Any,
    fetcher: Any = None,
    max_verifications: int = DEFAULT_VERIFICATIONS_PER_ISSUER,
) -> ScreeningResult:
    """Screen one verified issuer. Never raises."""
    from app.services.providers.leads import verify_lead
    from app.services.sources.publisher_tiers import publisher_tier

    result = ScreeningResult(ticker=issuer.ticker, exchange=issuer.exchange, status="failed")
    if provider is None:
        result.status = "unavailable"
        result.warnings.append("no external research provider is configured and enabled")
        return result
    try:
        answer = await provider.investigate(
            question=screening_question(issuer, intent),
            max_seconds=int(getattr(cfg, "v3_external_search_timeout_seconds", 0) or 180),
        )
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(f"screening investigation failed ({type(exc).__name__})")
        return result
    result.consumption.append(answer.consumption)
    result.warnings.extend(list(answer.warnings or [])[:4])
    leads = list(answer.research_leads or [])
    result.status = "partial" if answer.status != "completed" else "completed"

    # Verification budget spent where eligibility needs it most: size, growth, business.
    def _priority(lead: Any) -> int:
        metric = (lead.claimed_metric or "").lower()
        order = ("market cap", "shares outstanding", "share price", "organic", "revenue growth",
                 "revenue", "production growth", "capacity growth", "business", "headquarter",
                 "catalyst")
        return next((i for i, key in enumerate(order) if key in metric), len(order))

    leads.sort(key=_priority)
    terms = _terms(intent)
    revenues: list[tuple[Any, Any]] = []
    shares = price = None
    spent = 0
    for lead in leads:
        metric = (lead.claimed_metric or "").lower()
        record = {
            "metric": lead.claimed_metric,
            "claim": (lead.claim_text or "")[:300],
            "value": lead.claimed_value,
            "url": lead.claimed_source_url,
            "verified": False,
        }
        result.claims.append(record)
        if spent >= max_verifications or "catalyst" in metric:
            if "catalyst" in metric:
                result.catalysts.append({"claim": record["claim"], "url": record["url"],
                                         "verified": False})
            continue
        spent += 1
        outcome = await verify_lead(
            lead, cfg=cfg, fetcher=fetcher, allow_public_web=True,
            subject_name=issuer.name,
        )
        verified = bool(outcome.verified)
        record["verified"] = verified
        record["status"] = outcome.status
        if outcome.rejection_reason:
            record["rejection_reason"] = outcome.rejection_reason
        url = outcome.fetched_url or lead.claimed_source_url
        kind = publisher_kind(url, issuer.name)
        tier = kind or publisher_tier(url)
        record["source_tier"] = tier
        if not verified:
            continue
        passage = outcome.matched_excerpt or lead.claim_text or ""
        if "market cap" in metric:
            amount = _amount(lead.claimed_value, lead.claimed_unit)
            if amount:
                as_of, basis = _as_of(lead)
                result.market_cap = MarketCapObservation(
                    amount=amount, currency=_currency(lead.claimed_currency, lead.claimed_value),
                    as_of=as_of, as_of_basis=basis, source_url=url, source_tier=tier,
                    verified=True,
                )
        elif "shares outstanding" in metric:
            shares = (_amount(lead.claimed_value, lead.claimed_unit), url, tier)
        elif "share price" in metric:
            price = (
                _amount(lead.claimed_value, None),
                _currency(lead.claimed_currency, lead.claimed_value),
                url,
                tier,
                lead,
            )
        elif "organic" in metric and "growth" in metric:
            pct = _percent(lead.claimed_value)
            if pct is not None:
                result.growth.append(GrowthObservation(
                    "organic_revenue_growth", pct, lead.claimed_period, None, url, tier, True))
        elif "revenue growth" in metric or ("growth" in metric and "revenue" in metric):
            pct = _percent(lead.claimed_value)
            if pct is not None:
                result.growth.append(GrowthObservation(
                    "reported_revenue_growth", pct, lead.claimed_period, None, url, tier, True))
        elif "production growth" in metric or "capacity growth" in metric:
            pct = _percent(lead.claimed_value)
            if pct is not None:
                result.growth.append(GrowthObservation(
                    "production_growth" if "production" in metric else "capacity_growth",
                    pct, lead.claimed_period, None, url, tier, True))
        elif metric.startswith("revenue") or metric == "sales":
            revenues.append((lead, (url, tier)))
        elif "business" in metric or "product" in metric or "description" in metric:
            result.business_description = passage[:400]
            result.exposures.extend(
                exposures_from_text(passage, terms, source_url=url, source_tier=tier,
                                    verified=True)
            )
        elif "headquarter" in metric:
            country = _country_in(
                f"{lead.claimed_geography or ''} {lead.claimed_value or ''} {passage}"
            )
            if country:
                result.hq_country = country
                result.hq_source = {"url": url, "tier": tier}

    # Two verified revenue figures for different periods, same currency → a growth pair.
    if len(revenues) >= 2 and not any(g.metric != "revenue_pair" for g in result.growth):
        pairs = []
        for lead, (url, tier) in revenues:
            amount = _amount(lead.claimed_value, lead.claimed_unit)
            period = lead.claimed_period
            if amount and period:
                pairs.append((str(period), amount,
                              _currency(lead.claimed_currency, lead.claimed_value), url, tier))
        pairs.sort(key=lambda p: p[0], reverse=True)
        if len(pairs) >= 2 and pairs[0][0] != pairs[1][0] and pairs[0][2] == pairs[1][2]:
            obs = growth_from_revenue_pair(
                pairs[0][1], pairs[1][1], period=pairs[0][0], base_period=pairs[1][0],
                source_url=pairs[0][3], source_tier=pairs[0][4], verified=True,
            )
            if obs is not None:
                result.growth.append(obs)
    # Shares × price, both verified, when no market cap was stated.
    if result.market_cap is None and shares and price and shares[0] and price[0]:
        as_of, basis = _as_of(price[4])
        result.market_cap = MarketCapObservation(
            amount=shares[0] * price[0], currency=price[1], as_of=as_of, as_of_basis=basis,
            source_url=price[2], source_tier=price[3], verified=True, method="shares_x_price",
        )
    return result


def _country_in(text: str) -> str | None:
    from app.services.discovery.intent import _COUNTRY_WORDS

    low = f" {(text or '').lower()} "
    for word, country in _COUNTRY_WORDS.items():
        if len(word) > 3 and re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", low):
            return country
    return None


async def screen_issuers(
    issuers: list[IdentityOutcome],
    intent: DiscoveryIntent,
    *,
    cfg: Any,
    provider: Any = None,
    fetcher: Any = None,
) -> dict[str, ScreeningResult]:
    """Screen up to ``V3_DISCOVERY_MAX_SCREENED`` issuers with bounded concurrency."""
    from app.services.discovery.leads import _bounded

    if provider is None:
        from app.services.agents.routing import research_provider_for

        provider = research_provider_for(cfg)
    limit = _bounded(getattr(cfg, "v3_discovery_max_screened", None), DEFAULT_MAX_SCREENED,
                     HARD_MAX_SCREENED)
    concurrency = _bounded(getattr(cfg, "v3_discovery_screening_concurrency", None),
                           DEFAULT_CONCURRENCY, HARD_MAX_CONCURRENCY)
    per_issuer = _bounded(getattr(cfg, "v3_discovery_max_verifications_per_issuer", None),
                          DEFAULT_VERIFICATIONS_PER_ISSUER, 8)
    gate = asyncio.Semaphore(concurrency)

    async def _one(issuer: IdentityOutcome) -> tuple[str, ScreeningResult]:
        async with gate:
            return (
                f"{issuer.exchange}:{issuer.ticker}",
                await screen_issuer(issuer, intent, cfg=cfg, provider=provider,
                                    fetcher=fetcher, max_verifications=per_issuer),
            )

    pairs = await asyncio.gather(*(_one(i) for i in issuers[:limit]))
    return dict(pairs)


__all__ = [
    "ScreeningResult",
    "exposures_from_text",
    "screen_issuer",
    "screen_issuers",
    "screening_question",
]
