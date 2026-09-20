"""Vendor-neutral run consumption, and the budget that bounds it — V3.0 Slice 5.

WHY THIS MODULE EXISTS
======================
Two questions V3 cannot answer today, and both of them gate real decisions:

* **What does one research run actually consume?** OPEN DECISION #14 says the
  research-mode budgets should be "derived from measured live runs rather than
  guessing". Nothing measures them. Token counts exist per council attempt
  (``token_pacer.CouncilUsageTracker``) and are logged as events, but they are
  never aggregated to a run and never persisted, so "what did the Pandora run
  cost" is a question that can only be answered by grepping logs — and only for
  as long as the logs are retained.
* **Which provider is worth its price?** The benchmark metric the provider
  strategy is built around is ``cost_per_verified_finding``. Both halves of that
  fraction have to be recorded per run before any of it can be computed.

UNITS ARE VENDOR-NEUTRAL; MONEY IS DERIVED
==========================================
Consumption is counted in things that happen — tokens, calls, searches, fetches,
documents, pages, seconds — and cost is computed from a price book. A vendor
price change is then a configuration change, and historical runs stay comparable
instead of being denominated in a price that no longer exists.

ZERO IS NOT THE SAME AS "NOT MEASURED"
======================================
This is the one thing that would quietly corrupt every later comparison. Most of
the units below have no producer yet — there is no SearchProvider, no browser,
no OCR page counter on this path — and a record that reported ``web_search_calls:
0`` would be asserting that no searches happened, which is a different claim from
"nothing here counts searches". Every record therefore names which units were
actually instrumented when it was written, and a reader that ignores that field
is reading fabricated zeros.

The same discipline the fact-count work landed on: the rule is not to make the
numbers agree, it is to say which population each number counts.

BUDGETS ARE CHECKED BEFORE SPENDING
===================================
A run that discovers it is over budget after a $2 Deep Research call has not been
bounded; it has been audited. :meth:`ResearchBudget.check` is meant to be called
with what is ABOUT to be spent, and it names the limit that stopped the run —
because "budget exhausted" without saying which one is a dead end for whoever has
to tune it.

**Every limit defaults to unbounded, deliberately.** The numbers are OPEN
DECISIONS #13 (cost thresholds) and #14 (mode budgets), both owned by the user,
and #14 explicitly wants them derived from measurements this module is what
produces. Guessing a ceiling here and calling it a default would be inventing the
answer to a question that was asked of somebody else — and an invented ceiling
that silently truncates a research run is worse than none.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any

# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

#: Every unit name, in the order the provider strategy lists them. A unit is
#: added here and nowhere else, so a record and a budget cannot drift apart.
UNIT_NAMES: tuple[str, ...] = (
    "model_input_tokens",
    "model_output_tokens",
    "cached_tokens",
    "model_calls",
    "web_search_calls",
    "url_fetch_calls",
    "provider_research_runs",
    "documents_downloaded",
    "pages_parsed",
    "ocr_pages",
    "search_index_queries",
    "browser_minutes",
    "elapsed_seconds",
)


@dataclass(frozen=True)
class VendorUsage:
    """One vendor's model tokens, split into the classes it BILLS separately. V3.17.9.2.

    WHY THIS EXISTS
    ===============
    ``ConsumptionUnits.model_input_tokens`` is a sum across every vendor and every cache
    class, and no provider bills a sum. Measured on the first priced production run:
    6 of 7 calls went to DeepSeek and 1 to the research provider, and DeepSeek bills
    cache hits at **$0.006/M against $0.3/M for misses — fifty times apart**. Multiplying
    that sum by one rate does not produce an approximate cost; it produces a number whose
    error is unbounded in a direction nobody can see.

    ``cache_reported`` is the load-bearing field, and it is **not** a convenience flag.
    A provider that does not report its cache split leaves ``cached_input_tokens`` at 0,
    which is indistinguishable from "nothing hit the cache" — and billing all of it at
    the miss rate would overstate by up to 50x. Absent is not zero, here as everywhere
    else in this module: ``False`` makes the vendor's input **unpriceable**, and
    :func:`derive_cost` then answers ``None`` rather than guessing.
    """

    vendor: str
    calls: int = 0
    input_tokens: int = 0
    #: The subset of ``input_tokens`` that HIT the provider's context cache. Meaningful
    #: only when ``cache_reported`` is True.
    cached_input_tokens: int = 0
    output_tokens: int = 0
    #: Did the provider actually report the cache split for these calls?
    cache_reported: bool = False

    @property
    def uncached_input_tokens(self) -> int:
        return max(0, self.input_tokens - self.cached_input_tokens)

    def merge(self, other: "VendorUsage") -> "VendorUsage":
        """Add another record for the SAME vendor.

        ``cache_reported`` is an AND, not an OR: if any contributing call did not report
        its split, the merged total's split is not known either, and an OR here would
        quietly launder one unreported call into a fully-attributed sum.
        """
        if other.vendor != self.vendor:  # pragma: no cover - guarded by the caller
            raise ValueError("cannot merge usage for two different vendors")
        return VendorUsage(
            vendor=self.vendor,
            calls=self.calls + other.calls,
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_reported=self.cache_reported and other.cache_reported,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "vendor": self.vendor,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "uncached_input_tokens": self.uncached_input_tokens,
            "output_tokens": self.output_tokens,
            "cache_reported": self.cache_reported,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "VendorUsage | None":
        if not isinstance(raw, dict) or not raw.get("vendor"):
            return None
        return cls(
            vendor=str(raw["vendor"]),
            calls=int(raw.get("calls") or 0),
            input_tokens=int(raw.get("input_tokens") or 0),
            cached_input_tokens=int(raw.get("cached_input_tokens") or 0),
            output_tokens=int(raw.get("output_tokens") or 0),
            cache_reported=bool(raw.get("cache_reported")),
        )


def merge_vendor_usage(
    *groups: "tuple[VendorUsage, ...]",
) -> "tuple[VendorUsage, ...]":
    """Merge several per-vendor breakdowns into one, keyed by vendor, sorted by name."""
    merged: dict[str, VendorUsage] = {}
    for group in groups:
        for item in group:
            existing = merged.get(item.vendor)
            merged[item.vendor] = existing.merge(item) if existing else item
    return tuple(merged[k] for k in sorted(merged))


@dataclass(frozen=True)
class ConsumptionUnits:
    """What one research run consumed, in units that outlive a price list.

    ``tokens_estimated`` is carried rather than folded in: ``LLMUsage.estimated``
    already records when a count came from the ~4-chars/token heuristic instead
    of provider metadata, and presenting an estimate as a measurement is the same
    class of error as presenting a model claim as evidence.
    """

    model_input_tokens: int = 0
    model_output_tokens: int = 0
    cached_tokens: int = 0
    model_calls: int = 0
    web_search_calls: int = 0
    url_fetch_calls: int = 0
    provider_research_runs: int = 0
    documents_downloaded: int = 0
    pages_parsed: int = 0
    ocr_pages: int = 0
    search_index_queries: int = 0
    browser_minutes: float = 0.0
    elapsed_seconds: float = 0.0

    #: True when ANY contributing token count was a heuristic estimate.
    tokens_estimated: bool = False

    #: The units this record actually measured. A unit absent from here is
    #: NOT ZERO — it is unmeasured, and the difference decides whether a later
    #: comparison is meaningful.
    instrumented: frozenset[str] = frozenset()

    #: The model tokens above, split by the vendor that billed them. V3.17.9.2.
    #: Empty means "not broken down", which is a DIFFERENT statement from "one vendor" —
    #: see :func:`derive_cost`, which refuses to price a breakdown it does not have.
    by_vendor: tuple[VendorUsage, ...] = ()

    @property
    def model_tokens(self) -> int:
        return self.model_input_tokens + self.model_output_tokens

    def __add__(self, other: "ConsumptionUnits") -> "ConsumptionUnits":
        if not isinstance(other, ConsumptionUnits):  # pragma: no cover - guard
            return NotImplemented
        merged: dict[str, Any] = {}
        for name in UNIT_NAMES:
            merged[name] = getattr(self, name) + getattr(other, name)
        merged["tokens_estimated"] = self.tokens_estimated or other.tokens_estimated
        merged["instrumented"] = self.instrumented | other.instrumented
        merged["by_vendor"] = merge_vendor_usage(self.by_vendor, other.by_vendor)
        return ConsumptionUnits(**merged)

    def measured(self, name: str) -> bool:
        """Whether this record actually counted ``name``."""
        return name in self.instrumented

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {name: getattr(self, name) for name in UNIT_NAMES}
        out["tokens_estimated"] = self.tokens_estimated
        out["instrumented"] = sorted(self.instrumented)
        # Named explicitly rather than left for a reader to subtract, because
        # the whole point is that these zeros mean nothing.
        out["not_instrumented"] = sorted(set(UNIT_NAMES) - self.instrumented)
        out["by_vendor"] = [v.to_dict() for v in self.by_vendor]
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "ConsumptionUnits":
        raw = raw or {}
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            if f.name in ("instrumented", "by_vendor"):
                continue
            if f.name in raw:
                kwargs[f.name] = raw[f.name]
        instrumented = raw.get("instrumented") or []
        vendors = [VendorUsage.from_dict(v) for v in (raw.get("by_vendor") or [])]
        return cls(
            **kwargs,
            instrumented=frozenset(str(x) for x in instrumented),
            by_vendor=tuple(v for v in vendors if v is not None),
        )

    def with_instrumented(self, *names: str) -> "ConsumptionUnits":
        return replace(self, instrumented=self.instrumented | frozenset(names))


EMPTY_CONSUMPTION = ConsumptionUnits()


# ---------------------------------------------------------------------------
# Derived cost
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelPrice:
    """One vendor's token rates, in the classes that vendor actually bills. V3.17.9.2.

    Three rates, because DeepSeek's published table has three and so does Azure
    OpenAI's: uncached input, cached input, output. A vendor with no cache discount is
    configured with the same number twice, which is a statement rather than an omission.
    """

    usd_per_million_input: float | None = None
    usd_per_million_cached_input: float | None = None
    usd_per_million_output: float | None = None

    @property
    def is_complete(self) -> bool:
        """Can this price every class of token a vendor can return?"""
        return (
            self.usd_per_million_input is not None
            and self.usd_per_million_cached_input is not None
            and self.usd_per_million_output is not None
        )


@dataclass(frozen=True)
class PriceBook:
    """Unit prices, in USD. Empty by default, and that is not an oversight.

    An empty price book yields ``None`` rather than ``0.0``, because "we do not
    know what this cost" and "this cost nothing" are different statements and
    only one of them is true. Prices are configuration; they are never hard-coded
    here, and a price change must never be a code change.
    """

    usd_per_million_input_tokens: float | None = None
    usd_per_million_output_tokens: float | None = None
    usd_per_thousand_web_searches: float | None = None
    usd_per_thousand_url_fetches: float | None = None
    usd_per_provider_research_run: float | None = None
    usd_per_thousand_index_queries: float | None = None
    usd_per_browser_minute: float | None = None

    #: Per-vendor model rates. A tuple of pairs rather than a dict so the dataclass
    #: stays frozen and hashable. V3.17.9.2 — the flat ``usd_per_million_*`` fields
    #: above cannot express two vendors, and production runs two.
    vendor_rates: tuple[tuple[str, ModelPrice], ...] = ()

    def for_vendor(self, vendor: str) -> ModelPrice | None:
        """This vendor's rates, or ``None`` when none are configured for it.

        Matched case-insensitively on the exact vendor name the telemetry recorded. No
        prefix or fuzzy matching: a rate applied to the wrong vendor is the failure this
        whole type exists to prevent, and a near-match is how that happens.
        """
        wanted = (vendor or "").strip().lower()
        for name, price in self.vendor_rates:
            if name.strip().lower() == wanted:
                return price
        return None

    @property
    def is_empty(self) -> bool:
        flat = all(
            getattr(self, f.name) is None
            for f in fields(self)
            if f.name != "vendor_rates"
        )
        return flat and not self.vendor_rates


@dataclass(frozen=True)
class DerivedCost:
    """An ESTIMATE, and labelled as one.

    ``actual`` stays None until a provider reports a real charge. Keeping the two
    apart is the same rule the token counts follow: an estimate presented as a
    measurement is a small lie that compounds across a benchmark.
    """

    estimated_usd: float | None
    actual_usd: float | None = None
    #: Units that contributed nothing because the price book had no price for
    #: them. An estimate missing its dominant term is worse than no estimate.
    unpriced_units: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimated_usd": self.estimated_usd,
            "actual_usd": self.actual_usd,
            "unpriced_units": list(self.unpriced_units),
        }


def derive_cost(units: ConsumptionUnits, prices: PriceBook) -> DerivedCost:
    """Money from units. Returns ``None`` unless EVERY measured unit could be priced.

    THE RULE, AND WHY IT CHANGED IN V3.17.9.2
    =========================================
    This function used to return the sum of whatever it *could* price and list the rest
    in ``unpriced_units``. A caller storing that number stores a **subtotal labelled as a
    cost** — and ``research_run_consumption.estimated_cost_usd`` is read by a budget cap,
    which then passes on spend it never saw. A partial total is not a total. If anything
    measured is unpriced, the answer is ``None``.

    MODEL TOKENS ARE PRICED PER VENDOR, PER CACHE CLASS
    ===================================================
    ``model_input_tokens`` is a sum across vendors and cache classes, and no provider
    bills a sum. When ``units.by_vendor`` is present each vendor's tokens are priced at
    that vendor's own rates. When it is absent the flat rates apply — which is correct
    only for a single-vendor, cache-free record, and is the legacy path.

    **A vendor that did not report its cache split cannot be priced at all.** Its input
    tokens are billed at two rates up to fifty times apart and nothing says how they
    divide, so guessing either one is a fabrication. ``None`` is the honest answer.
    """
    if prices.is_empty:
        return DerivedCost(estimated_usd=None, unpriced_units=tuple(sorted(UNIT_NAMES)))

    total = 0.0
    priced_any = False
    unpriced: list[str] = []

    def add(unit: str, quantity: float, price: float | None, per: float) -> None:
        nonlocal total, priced_any
        if quantity <= 0:
            return
        if price is None:
            unpriced.append(unit)
            return
        total += quantity / per * price
        priced_any = True

    # ONE vendor and a flat rate is exactly what the flat setting means, so a record
    # that carries a single-vendor breakdown and no per-vendor rate falls back to it.
    # TWO vendors cannot: one rate applied to both is wrong whatever was configured,
    # because only one rate was given. The fallback keeps an environment configured
    # before V3.17.9.2 priced, instead of silently going unknown the day a breakdown
    # started being recorded.
    flat_fallback = (
        len(units.by_vendor) == 1
        and prices.for_vendor(units.by_vendor[0].vendor) is None
        and (
            prices.usd_per_million_input_tokens is not None
            or prices.usd_per_million_output_tokens is not None
        )
    )

    if units.by_vendor and not flat_fallback:
        for usage in units.by_vendor:
            rate = prices.for_vendor(usage.vendor)
            if rate is None:
                if usage.input_tokens > 0 or usage.output_tokens > 0:
                    unpriced.append(f"model_tokens[{usage.vendor}:no_rate_configured]")
                continue
            add(
                f"model_output_tokens[{usage.vendor}]",
                usage.output_tokens,
                rate.usd_per_million_output,
                1_000_000,
            )
            if usage.input_tokens <= 0:
                continue
            if not usage.cache_reported:
                # Absent is not zero. Billing all of it at the miss rate would overstate
                # by up to the full cache discount, and at the hit rate would understate
                # by the same. Neither is an estimate.
                unpriced.append(f"model_input_tokens[{usage.vendor}:cache_unreported]")
                continue
            add(
                f"model_input_tokens[{usage.vendor}:uncached]",
                usage.uncached_input_tokens,
                rate.usd_per_million_input,
                1_000_000,
            )
            add(
                f"model_input_tokens[{usage.vendor}:cached]",
                usage.cached_input_tokens,
                rate.usd_per_million_cached_input,
                1_000_000,
            )
    else:
        add(
            "model_input_tokens",
            units.model_input_tokens,
            prices.usd_per_million_input_tokens,
            1_000_000,
        )
        add(
            "model_output_tokens",
            units.model_output_tokens,
            prices.usd_per_million_output_tokens,
            1_000_000,
        )

    add(
        "web_search_calls",
        units.web_search_calls,
        prices.usd_per_thousand_web_searches,
        1_000,
    )
    add(
        "url_fetch_calls",
        units.url_fetch_calls,
        prices.usd_per_thousand_url_fetches,
        1_000,
    )
    add(
        "provider_research_runs",
        units.provider_research_runs,
        prices.usd_per_provider_research_run,
        1,
    )
    add(
        "search_index_queries",
        units.search_index_queries,
        prices.usd_per_thousand_index_queries,
        1_000,
    )
    add("browser_minutes", units.browser_minutes, prices.usd_per_browser_minute, 1)

    # A partial total is not a total. See the docstring: the one caller that matters
    # here feeds a budget cap.
    known = priced_any and not unpriced
    return DerivedCost(
        estimated_usd=round(total, 6) if known else None,
        unpriced_units=tuple(sorted(unpriced)),
    )


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


class BudgetExceeded(Exception):
    """A limit was reached. Names WHICH one, because that is the actionable part."""

    def __init__(self, limit: str, spent: float, allowed: float) -> None:
        super().__init__(
            f"research budget limit {limit!r} reached: {spent} of {allowed} allowed"
        )
        self.limit = limit
        self.spent = spent
        self.allowed = allowed

    #: Permanent for the worker's taxonomy: a run that hit its ceiling will hit
    #: it again on a retry, and spending the budget twice to prove it is exactly
    #: what the ceiling exists to prevent.
    job_transient = False


#: A limit of 0 means UNBOUNDED. Chosen over ``None`` so a config value read from
#: the environment (where everything is a string) has one obvious "off" value.
UNBOUNDED = 0


@dataclass(frozen=True)
class ResearchBudget:
    """What one run may spend before it must stop.

    Every bound defaults to :data:`UNBOUNDED` **at this layer**, and that is no longer
    where the numbers live. ADR-052 resolved OPEN DECISIONS #13/#14 with bounded
    technical defaults per research mode, and ``app.services.research_mode.budget_for``
    builds the budget a run actually gets — finite in every dimension except
    ``max_external_cost_usd``, which stays unset because money is derived from a price
    book nobody has filled in.

    This class keeps permissive defaults on purpose: it is the *enforcement mechanism*,
    and a mechanism that refused to represent "no limit on this dimension" could not
    express the one limit the decision deliberately left open.
    """

    max_model_calls: int = UNBOUNDED
    max_model_tokens: int = UNBOUNDED
    max_web_searches: int = UNBOUNDED
    max_documents: int = UNBOUNDED
    max_browser_minutes: float = UNBOUNDED
    max_wall_seconds: float = UNBOUNDED
    max_external_cost_usd: float = UNBOUNDED

    #: Names of the limits actually in force, for the record and for a reader.
    @property
    def active_limits(self) -> tuple[str, ...]:
        return tuple(
            f.name
            for f in fields(self)
            if float(getattr(self, f.name) or 0) > 0
        )

    @property
    def is_unbounded(self) -> bool:
        return not self.active_limits

    def check(
        self,
        spent: ConsumptionUnits,
        *,
        about_to_spend: ConsumptionUnits | None = None,
        estimated_cost_usd: float | None = None,
    ) -> None:
        """Raise :class:`BudgetExceeded` if this spend would break a limit.

        Called BEFORE the spend. The projected total is what is tested, because
        a check that only looks at what has already happened turns a budget into
        a report.
        """
        projected = spent + (about_to_spend or EMPTY_CONSUMPTION)
        self._assert("max_model_calls", projected.model_calls)
        self._assert("max_model_tokens", projected.model_tokens)
        self._assert("max_web_searches", projected.web_search_calls)
        self._assert("max_documents", projected.documents_downloaded)
        self._assert("max_browser_minutes", projected.browser_minutes)
        self._assert("max_wall_seconds", projected.elapsed_seconds)
        if estimated_cost_usd is not None:
            self._assert("max_external_cost_usd", estimated_cost_usd)

    def _assert(self, limit: str, spent: float) -> None:
        allowed = float(getattr(self, limit) or 0)
        if allowed <= 0:
            return
        if spent > allowed:
            raise BudgetExceeded(limit, spent, allowed)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {f.name: getattr(self, f.name) for f in fields(self)}
        out["active_limits"] = list(self.active_limits)
        return out


def budget_from_settings(cfg: Any | None = None) -> ResearchBudget:
    """The configured budget. Unbounded unless the user has set numbers."""
    if cfg is None:
        from app.core.config import settings as cfg  # noqa: PLW0127

    return ResearchBudget(
        max_model_calls=int(getattr(cfg, "v3_run_max_model_calls", 0) or 0),
        max_model_tokens=int(getattr(cfg, "v3_run_max_model_tokens", 0) or 0),
        max_web_searches=int(getattr(cfg, "v3_run_max_web_searches", 0) or 0),
        max_documents=int(getattr(cfg, "v3_run_max_documents", 0) or 0),
        max_browser_minutes=float(getattr(cfg, "v3_run_max_browser_minutes", 0) or 0),
        max_wall_seconds=float(getattr(cfg, "v3_run_max_wall_seconds", 0) or 0),
        max_external_cost_usd=float(
            getattr(cfg, "v3_run_max_external_cost_usd", 0) or 0
        ),
    )


def _vendor_rates_from_settings(cfg: Any) -> tuple[tuple[str, ModelPrice], ...]:
    """Parse ``V3_PRICE_VENDOR_RATES``. Never raises; a bad value yields no rates.

    A malformed price book that raised would take down every research run over a typo in
    a setting. A malformed price book that guessed would produce a wrong number, which is
    worse. It yields **nothing**, so the cost reports as unknown and the log says why.
    """
    import json

    raw = (getattr(cfg, "v3_price_vendor_rates", "") or "").strip()
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return ()
    if not isinstance(parsed, dict):
        return ()

    def rate(block: Any, key: str) -> float | None:
        if not isinstance(block, dict):
            return None
        try:
            value = float(block.get(key))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        # A ZERO rate is accepted as a real price: a free tier is a fact, and coercing it
        # to "unknown" would report a free vendor as unpriceable. Negative is not.
        return value if value >= 0 else None

    out: list[tuple[str, ModelPrice]] = []
    for vendor, block in parsed.items():
        if not isinstance(vendor, str) or not vendor.strip():
            continue
        out.append(
            (
                vendor.strip(),
                ModelPrice(
                    usd_per_million_input=rate(block, "input_per_million"),
                    usd_per_million_cached_input=rate(block, "cached_input_per_million"),
                    usd_per_million_output=rate(block, "output_per_million"),
                ),
            )
        )
    return tuple(out)


def price_book_from_settings(cfg: Any | None = None) -> PriceBook:
    """The configured price book. Empty unless the user has entered prices."""
    if cfg is None:
        from app.core.config import settings as cfg  # noqa: PLW0127

    def price(name: str) -> float | None:
        raw = getattr(cfg, name, 0.0) or 0.0
        return float(raw) if float(raw) > 0 else None

    return PriceBook(
        vendor_rates=_vendor_rates_from_settings(cfg),
        usd_per_million_input_tokens=price("v3_price_per_million_input_tokens"),
        usd_per_million_output_tokens=price("v3_price_per_million_output_tokens"),
        usd_per_thousand_web_searches=price("v3_price_per_thousand_web_searches"),
        usd_per_thousand_url_fetches=price("v3_price_per_thousand_url_fetches"),
        usd_per_provider_research_run=price("v3_price_per_provider_research_run"),
        usd_per_thousand_index_queries=price("v3_price_per_thousand_index_queries"),
        usd_per_browser_minute=price("v3_price_per_browser_minute"),
    )


# ---------------------------------------------------------------------------
# Producers
# ---------------------------------------------------------------------------

#: The units the company-research path measures TODAY. Everything else in
#: ``UNIT_NAMES`` has no producer yet — there is no SearchProvider, no browser,
#: no per-page counter — and must be reported as unmeasured rather than zero.
COUNCIL_INSTRUMENTED: frozenset[str] = frozenset(
    {
        "model_input_tokens",
        "model_output_tokens",
        "model_calls",
        "documents_downloaded",
    }
)

RUN_INSTRUMENTED: frozenset[str] = frozenset({"elapsed_seconds"})


def council_consumption(result: Any) -> ConsumptionUnits:
    """Consumption of one council run, read off its own result.

    Tolerant by construction: a council result produced before this field existed
    reports nothing measured, which is the honest answer rather than zeros.
    """
    raw = getattr(result, "consumption", None)
    if isinstance(raw, dict) and raw:
        return ConsumptionUnits.from_dict(raw)
    return EMPTY_CONSUMPTION


def from_usage_tracker(
    tracker: Any, *, documents: int = 0
) -> ConsumptionUnits:
    """Turn a ``CouncilUsageTracker`` into vendor-neutral units."""
    if tracker is None:
        return EMPTY_CONSUMPTION
    calls = sum(int(v) for v in (getattr(tracker, "attempts_by_agent", {}) or {}).values())
    return ConsumptionUnits(
        model_input_tokens=int(getattr(tracker, "prompt_tokens", 0) or 0),
        model_output_tokens=int(getattr(tracker, "completion_tokens", 0) or 0),
        model_calls=calls,
        documents_downloaded=int(documents),
        tokens_estimated=bool(getattr(tracker, "any_estimated", False)),
        instrumented=COUNCIL_INSTRUMENTED,
    )


def run_record(
    units: ConsumptionUnits,
    *,
    budget: ResearchBudget | None = None,
    prices: PriceBook | None = None,
) -> dict[str, Any]:
    """The JSON record persisted for one run, units and derived cost together."""
    budget = budget if budget is not None else ResearchBudget()
    prices = prices if prices is not None else PriceBook()
    return {
        "units": units.to_dict(),
        "cost": derive_cost(units, prices).to_dict(),
        "budget": budget.to_dict(),
    }
