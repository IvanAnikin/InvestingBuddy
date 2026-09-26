"""Constraint verification and eligibility — V3.19.3.

A user's words are a filter HYPOTHESIS. They become an attribute of a company here, and
only here, and only from an observation that carries its source:

* SIZE      — a verified market cap, converted with an official FX rate, placed in a
              declared USD band;
* GROWTH    — business growth (organic, reported, a revenue pair; production or capacity
              for a pre-revenue producer). Price momentum is not an input: nothing in this
              module can receive it (``GROWTH_METRICS`` is closed);
* GEOGRAPHY — the listing venue's country or the verified headquarters;
* INDUSTRY  — what verified evidence says the company does, graded affirmatively (a
              denial is not exposure — V3.18.14).

Every result is ``pass`` | ``fail`` | ``unknown`` | ``not_requested``. ``pass`` exists only
with a value and a source. ``decide_eligibility`` is the only place a candidate is included
or excluded, and a council never overrides it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from app.services.discovery.fx import FxRate, to_usd
from app.services.discovery.intent import HARD, SIZE_BUCKETS, Constraint

PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"
NOT_REQUESTED = "not_requested"

# ── Size ───────────────────────────────────────────────────────────────────── #

#: Declared USD bands (lower inclusive, upper exclusive). One table for the platform:
#: ``director.thesis.SIZE_BANDS`` is derived from it.
SIZE_BANDS_USD: dict[str, tuple[float | None, float | None]] = {
    "micro_cap": (None, 300e6),
    "small_cap": (300e6, 2e9),
    "mid_cap": (2e9, 10e9),
    "large_cap": (10e9, 200e9),
    "mega_cap": (200e9, None),
}
#: Within this fraction of a band edge a size is flagged borderline (shown, not decisive).
BORDERLINE_FRACTION = 0.15


def size_bucket(usd: float) -> str:
    for bucket in SIZE_BUCKETS:
        low, high = SIZE_BANDS_USD[bucket]
        if (low is None or usd >= low) and (high is None or usd < high):
            return bucket
    return "mega_cap"  # pragma: no cover - the bands are exhaustive


def _borderline(usd: float) -> bool:
    for low, high in SIZE_BANDS_USD.values():
        for edge in (low, high):
            if edge and abs(usd - edge) <= edge * BORDERLINE_FRACTION:
                return True
    return False


@dataclass(frozen=True)
class MarketCapObservation:
    """A market capitalisation a page fetched BY THE PLATFORM states (or shares × price).

    ``verified`` is True only when the figure was located on bytes the platform fetched.
    """

    amount: float
    currency: str | None
    as_of: str | None
    as_of_basis: str  # "stated" | "fetch_date"
    source_url: str | None
    source_tier: str | None
    verified: bool
    method: str = "stated_market_cap"  # | "shares_x_price"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Growth ─────────────────────────────────────────────────────────────────── #

#: The ONLY growth measures. A price return is not in this set, and ``GrowthObservation``
#: refuses to be constructed with anything else.
GROWTH_METRICS: tuple[str, ...] = (
    "organic_revenue_growth",
    "reported_revenue_growth",
    "revenue_pair",
    "production_growth",
    "capacity_growth",
)
#: Preference order: the measure a reader of the company's own reports would use first.
_GROWTH_PREFERENCE = {m: i for i, m in enumerate(GROWTH_METRICS)}
HIGH_GROWTH_PCT = 15.0

GROWTH_ESTABLISHED = "established"
GROWTH_DECLINING = "declining"
GROWTH_MIXED = "mixed"
GROWTH_NOT_ESTABLISHED = "not_established"


@dataclass(frozen=True)
class GrowthObservation:
    metric: str
    growth_pct: float
    period: str | None
    base_period: str | None
    source_url: str | None
    source_tier: str | None
    verified: bool
    note: str | None = None

    def __post_init__(self) -> None:
        if self.metric not in GROWTH_METRICS:
            raise ValueError(
                f"{self.metric!r} is not a business-growth measure; growth is never "
                "inferred from share-price movement"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def growth_from_revenue_pair(
    current: float, prior: float, *, period: str | None, base_period: str | None,
    source_url: str | None, source_tier: str | None, verified: bool,
) -> GrowthObservation | None:
    """Two revenue figures for consecutive periods → one observation. None if unusable."""
    if prior is None or current is None or prior <= 0:
        return None
    return GrowthObservation(
        "revenue_pair", round((current / prior - 1.0) * 100.0, 2), period, base_period,
        source_url, source_tier, verified,
        note=f"computed from revenue {current:g} ({period}) vs {prior:g} ({base_period})",
    )


def classify_growth(
    observations: Iterable[GrowthObservation],
) -> tuple[str, GrowthObservation | None, str]:
    """``(status, the observation chosen, basis)`` from VERIFIED observations only."""
    verified = [o for o in observations if o.verified]
    if not verified:
        return GROWTH_NOT_ESTABLISHED, None, "no verified business-growth measure"
    verified.sort(key=lambda o: _GROWTH_PREFERENCE[o.metric])
    chosen = verified[0]
    organic = next((o for o in verified if o.metric == "organic_revenue_growth"), None)
    reported = next(
        (o for o in verified if o.metric in ("reported_revenue_growth", "revenue_pair")), None
    )
    if (
        organic is not None
        and reported is not None
        and (organic.growth_pct > 0) != (reported.growth_pct > 0)
        and organic.growth_pct != 0
        and reported.growth_pct != 0
    ):
        return (
            GROWTH_MIXED,
            chosen,
            f"organic {organic.growth_pct:+.1f}% and reported {reported.growth_pct:+.1f}% "
            "disagree in direction",
        )
    label = chosen.metric.replace("_", " ")
    period = f" ({chosen.period})" if chosen.period else ""
    if chosen.growth_pct > 0:
        return GROWTH_ESTABLISHED, chosen, f"{label} {chosen.growth_pct:+.1f}%{period}"
    if chosen.growth_pct < 0:
        return GROWTH_DECLINING, chosen, f"{label} {chosen.growth_pct:+.1f}%{period}"
    return GROWTH_MIXED, chosen, f"{label} flat{period}"


# ── Industry / theme exposure ──────────────────────────────────────────────── #

EXPOSURE_DIRECT = "direct"
EXPOSURE_INDIRECT = "indirect"
EXPOSURE_DENIED = "denied"


@dataclass(frozen=True)
class ExposureObservation:
    """A VERIFIED statement about what the company does, and the term it evidences."""

    term: str
    exposure: str  # direct | indirect | denied
    statement: str
    source_url: str | None
    source_tier: str | None
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Results ────────────────────────────────────────────────────────────────── #


@dataclass
class ConstraintResult:
    key: str
    requested: list[str]
    hardness: str
    status: str
    value: dict[str, Any] | None = None
    basis: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    borderline: bool = False

    def __post_init__(self) -> None:
        # The invariant, enforced where the result is made: a pass has evidence.
        if self.status == PASS and (not self.value or not self.sources):
            raise ValueError(f"a '{self.key}' pass needs a value and a source")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _source(url: str | None, tier: str | None, **extra: Any) -> dict[str, Any]:
    return {"url": url, "tier": tier, **{k: v for k, v in extra.items() if v is not None}}


def verify_size(
    constraint: Constraint | None,
    observation: MarketCapObservation | None,
    rate: FxRate | None,
    *,
    fx_unavailable_reason: str | None = None,
) -> ConstraintResult:
    requested = list(constraint.requested) if constraint else []
    hardness = constraint.hardness if constraint else HARD
    status_if_known = NOT_REQUESTED if constraint is None else None
    if observation is None or not observation.verified:
        return ConstraintResult(
            "size", requested, hardness, status_if_known or UNKNOWN,
            basis=(
                "no market capitalisation was verified on a page the platform fetched"
                if observation is None
                else "a market capitalisation was claimed but not verified"
            ),
        )
    if rate is None:
        return ConstraintResult(
            "size", requested, hardness, status_if_known or UNKNOWN,
            value={"amount": observation.amount, "currency": observation.currency},
            basis=fx_unavailable_reason or "no official FX rate for the stated currency",
            sources=[_source(observation.source_url, observation.source_tier)],
        )
    usd = to_usd(observation.amount, observation.currency, rate)
    bucket = size_bucket(usd)
    value = {
        "amount": observation.amount,
        "currency": observation.currency,
        "usd": round(usd, 0),
        "bucket": bucket,
        "as_of": observation.as_of,
        "as_of_basis": observation.as_of_basis,
        "method": observation.method,
        "fx": rate.to_dict(),
    }
    sources = [_source(observation.source_url, observation.source_tier, as_of=observation.as_of)]
    if rate.series:
        sources.append(_source(rate.source_url, rate.tier, as_of=rate.rate_date))
    if constraint is None:
        return ConstraintResult(
            "size", [], HARD, NOT_REQUESTED, value=value,
            basis=f"verified size {bucket.replace('_', '-')}", sources=sources,
            borderline=_borderline(usd),
        )
    fits = bucket in constraint.requested
    return ConstraintResult(
        "size",
        requested,
        hardness,
        PASS if fits else FAIL,
        value=value,
        basis=(
            f"market cap {observation.amount:,.0f} {observation.currency} ≈ "
            f"${usd / 1e9:,.2f}bn → {bucket.replace('_', '-')}"
            + ("" if fits else f"; requested {', '.join(constraint.requested)}")
        ),
        sources=sources,
        borderline=_borderline(usd),
    )


def verify_growth(
    constraint: Constraint | None, observations: Sequence[GrowthObservation]
) -> ConstraintResult:
    status, chosen, basis = classify_growth(observations)
    value = None
    sources: list[dict[str, Any]] = []
    if chosen is not None:
        value = {"growth_status": status, **chosen.to_dict()}
        sources = [_source(chosen.source_url, chosen.source_tier, period=chosen.period)]
    if constraint is None:
        return ConstraintResult(
            "growth", [], HARD, NOT_REQUESTED,
            value=value or {"growth_status": status}, basis=basis, sources=sources,
        )
    wanted_high = "high_growth" in constraint.requested
    if status == GROWTH_ESTABLISHED and chosen is not None:
        if wanted_high and chosen.growth_pct < HIGH_GROWTH_PCT:
            result = FAIL
            basis += f"; high growth requires ≥ {HIGH_GROWTH_PCT:.0f}%"
        else:
            result = PASS
    elif status == GROWTH_DECLINING:
        result = FAIL
    else:
        result = UNKNOWN
    return ConstraintResult(
        "growth", list(constraint.requested), constraint.hardness, result,
        value=value or {"growth_status": status}, basis=basis, sources=sources,
    )


def verify_geography(
    constraint: Constraint | None,
    *,
    listing_country: str | None,
    listing_source: dict[str, Any] | None,
    hq_country: str | None = None,
    hq_source: dict[str, Any] | None = None,
) -> ConstraintResult:
    from app.services.exchange_registry import region_for_country

    observed = [c for c in (listing_country, hq_country) if c]
    if constraint is None:
        return ConstraintResult(
            "geography", [], HARD, NOT_REQUESTED,
            value=(
                {"listing_country": listing_country, "hq_country": hq_country}
                if observed
                else None
            ),
        )
    requested = set(constraint.requested)
    sources = [s for s in (listing_source, hq_source) if s]
    if not observed:
        return ConstraintResult(
            "geography", list(constraint.requested), constraint.hardness, UNKNOWN,
            basis="neither the listing country nor the headquarters is known",
        )
    value = {
        "listing_country": listing_country,
        "listing_region": region_for_country(listing_country) if listing_country else None,
        "hq_country": hq_country,
    }
    # V3.19.2 — what the user EXCLUDED ("non-US", "Europe excluding the UK") fails,
    # whatever else matches: a UK company is in Europe and still not wanted.
    excluded = set(constraint.excluded)
    hit = [c for c in observed if c in excluded or region_for_country(c) in excluded]
    if hit:
        return ConstraintResult(
            "geography", list(constraint.requested), constraint.hardness, FAIL,
            value=value, basis=f"{hit[0]} is excluded by the thesis", sources=sources,
        )
    # Only exclusions named: anywhere else is in the requested geography.
    matched = (
        [c for c in observed if c in requested or region_for_country(c) in requested]
        if requested
        else list(observed)
    )
    if matched and sources:
        return ConstraintResult(
            "geography", list(constraint.requested), constraint.hardness, PASS,
            value=value, basis=f"{matched[0]} is in the requested geography", sources=sources,
        )
    if matched:
        return ConstraintResult(
            "geography", list(constraint.requested), constraint.hardness, UNKNOWN,
            value=value, basis="the geography matches but its source is not recorded",
        )
    return ConstraintResult(
        "geography", list(constraint.requested), constraint.hardness, FAIL,
        value=value, basis=f"{', '.join(observed)} is outside {', '.join(constraint.requested)}",
        sources=sources,
    )


def verify_industry(
    constraint: Constraint | None,
    exposures: Sequence[ExposureObservation],
    *,
    registry_match: dict[str, Any] | None = None,
) -> ConstraintResult:
    """Theme / material / industry fit, from VERIFIED statements of what the company does.

    ``registry_match`` is a held classification (curated registry or platform company row)
    naming a requested theme — reference data already on record, tier T3.
    """
    if constraint is None:
        return ConstraintResult("industry", [], HARD, NOT_REQUESTED)
    requested = list(constraint.requested)
    verified = [e for e in exposures if e.verified and e.term in constraint.requested]
    affirmed = [e for e in verified if e.exposure in (EXPOSURE_DIRECT, EXPOSURE_INDIRECT)]
    if affirmed:
        direct = [e for e in affirmed if e.exposure == EXPOSURE_DIRECT]
        chosen = (direct or affirmed)[0]
        return ConstraintResult(
            "industry", requested, constraint.hardness, PASS,
            value={
                "matched": sorted({e.term for e in affirmed}),
                "exposure": EXPOSURE_DIRECT if direct else EXPOSURE_INDIRECT,
                "statement": chosen.statement[:300],
            },
            basis=f"{chosen.exposure} exposure to {chosen.term.replace('_', ' ')}",
            sources=[_source(e.source_url, e.source_tier) for e in (direct or affirmed)[:3]],
        )
    if registry_match:
        return ConstraintResult(
            "industry", requested, constraint.hardness, PASS,
            value={"matched": [registry_match.get("theme")], "exposure": "classified",
                   "classification": registry_match.get("industry")},
            basis=(
                f"classified {registry_match.get('industry')} in "
                f"{registry_match.get('source')}"
            ),
            sources=[_source(None, registry_match.get("tier"),
                             registry=registry_match.get("source"))],
        )
    if verified and all(e.exposure == EXPOSURE_DENIED for e in verified):
        return ConstraintResult(
            "industry", requested, constraint.hardness, FAIL,
            value={"denied": sorted({e.term for e in verified})},
            basis="verified evidence denies exposure to the requested theme",
            sources=[_source(e.source_url, e.source_tier) for e in verified[:3]],
        )
    return ConstraintResult(
        "industry", requested, constraint.hardness, UNKNOWN,
        basis="no verified statement of what the company does names the requested theme",
    )


def verify_listing(identity: dict[str, Any] | None) -> ConstraintResult:
    status = (identity or {}).get("identity_status")
    source = (identity or {}).get("listing_source") or {}
    if status in ("verified", "platform_registry"):
        return ConstraintResult(
            "listing", ["listed_security"], HARD, PASS,
            value={
                "ticker": (identity or {}).get("ticker"),
                "exchange": (identity or {}).get("exchange"),
                "identity_status": status,
            },
            basis=(
                "listing verified on a page the platform fetched"
                if status == "verified"
                else "a company already held in the platform registry"
            ),
            sources=[_source(source.get("url"), source.get("tier"))]
            if source
            else [_source(None, "T3_curated_reference_list", registry="platform")],
        )
    return ConstraintResult(
        "listing", ["listed_security"], HARD, FAIL,
        basis=(
            "identity not verified ("
            + str((identity or {}).get("rejection_reason") or "no evidence")
            + ")"
        ),
    )


# ── Eligibility ────────────────────────────────────────────────────────────── #

ELIGIBLE = "eligible"
INCLUDED_WITH_MISMATCH = "included_with_mismatch"
ELIGIBLE_UNVERIFIED = "eligible_unverified"
EXCLUDED = "excluded"

ELIGIBILITY_ORDER = {ELIGIBLE: 0, INCLUDED_WITH_MISMATCH: 1, ELIGIBLE_UNVERIFIED: 2, EXCLUDED: 3}


@dataclass
class Eligibility:
    status: str
    reasons: list[str]
    hard_passes: int
    soft_passes: int
    unknown_hard: list[str]
    failed_soft: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide_eligibility(results: Sequence[ConstraintResult]) -> Eligibility:
    """The deterministic inclusion decision. Nothing downstream may override it."""
    requested = [r for r in results if r.status != NOT_REQUESTED]
    hard = [r for r in requested if r.hardness == HARD]
    soft = [r for r in requested if r.hardness != HARD]
    failed_hard = [r for r in hard if r.status == FAIL]
    unknown_hard = [r.key for r in hard if r.status == UNKNOWN]
    failed_soft = [r.key for r in soft if r.status == FAIL]
    hard_passes = sum(1 for r in hard if r.status == PASS)
    soft_passes = sum(1 for r in soft if r.status == PASS)
    listing = next((r for r in results if r.key == "listing"), None)
    if listing is not None and listing.status != PASS:
        return Eligibility(EXCLUDED, [f"identity_unverified: {listing.basis}"], hard_passes,
                           soft_passes, unknown_hard, failed_soft)
    if failed_hard:
        return Eligibility(
            EXCLUDED,
            [f"{r.key}: {r.basis}" for r in failed_hard],
            hard_passes, soft_passes, unknown_hard, failed_soft,
        )
    if unknown_hard:
        return Eligibility(
            ELIGIBLE_UNVERIFIED,
            [f"{k}: not verified" for k in unknown_hard]
            + [f"{k}: soft preference not met" for k in failed_soft],
            hard_passes, soft_passes, unknown_hard, failed_soft,
        )
    if failed_soft:
        return Eligibility(
            INCLUDED_WITH_MISMATCH,
            [f"{k}: soft preference not met" for k in failed_soft],
            hard_passes, soft_passes, unknown_hard, failed_soft,
        )
    return Eligibility(ELIGIBLE, [], hard_passes, soft_passes, unknown_hard, failed_soft)


def ranking_key(eligibility: Eligibility, thesis_relevance: float | None = None) -> tuple:
    """Sort key: eligibility first, then verified hard passes, soft passes, relevance."""
    return (
        ELIGIBILITY_ORDER.get(eligibility.status, 9),
        -eligibility.hard_passes,
        -eligibility.soft_passes,
        -(thesis_relevance or 0.0),
    )


# ── Verified attributes for prose ──────────────────────────────────────────── #


def verified_attributes(results: Sequence[ConstraintResult]) -> dict[str, Any]:
    """What may be SAID about a candidate: only attributes established from evidence."""
    out: dict[str, Any] = {}
    for r in results:
        if r.key == "size" and r.value and r.value.get("bucket") and r.sources:
            out["size_bucket"] = r.value["bucket"]
            out["market_cap_usd"] = r.value.get("usd")
            out["market_cap"] = {
                k: r.value.get(k) for k in ("amount", "currency", "as_of", "as_of_basis")
            }
            out["size_borderline"] = r.borderline
        elif r.key == "growth" and r.value and r.value.get("growth_status"):
            out["growth_status"] = r.value["growth_status"]
            if r.value.get("metric"):
                out["growth_basis"] = r.basis
        elif r.key == "geography" and r.value and r.status == PASS:
            out["geography"] = r.value
        elif r.key == "industry" and r.status == PASS and r.value:
            out["industry_exposure"] = r.value
    return out


_BUCKET_WORDS = re.compile(r"(micro|small|mid|large|mega)[\s-]?caps?", re.IGNORECASE)


def bucket_from_words(text: str) -> str | None:
    """The size bucket a phrase names ("small-cap" → ``small_cap``), or None."""
    match = _BUCKET_WORDS.search(text or "")
    return f"{match.group(1).lower()}_cap" if match else None


__all__ = [
    "ConstraintResult",
    "ELIGIBLE",
    "ELIGIBLE_UNVERIFIED",
    "EXCLUDED",
    "Eligibility",
    "ExposureObservation",
    "FAIL",
    "GROWTH_METRICS",
    "GrowthObservation",
    "INCLUDED_WITH_MISMATCH",
    "MarketCapObservation",
    "NOT_REQUESTED",
    "PASS",
    "SIZE_BANDS_USD",
    "UNKNOWN",
    "bucket_from_words",
    "classify_growth",
    "decide_eligibility",
    "growth_from_revenue_pair",
    "ranking_key",
    "size_bucket",
    "verified_attributes",
    "verify_geography",
    "verify_growth",
    "verify_industry",
    "verify_listing",
    "verify_size",
]
