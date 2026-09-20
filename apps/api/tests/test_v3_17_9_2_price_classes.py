"""V3.17.9.2 — tokens are priced at the rate they were billed at, or not at all.

WHAT THIS FILE EXISTS FOR
=========================
V3.17.9.1 proved consumption is measured and attributable to its durable job. The next
step was to configure a price book — and the price book could not represent what the
provider actually bills.

Measured on the first priced production run (job ``18cbada9-…``, report ``ff7cbfb2-…``):

    model_by_vendor      {"deepseek": {"calls": 6, "input": 20892, "output": 7137}}
    provider leg         1 call, 590 input, 257 output, 384 CACHED
    routing.slots        chair + red_team -> azure_openai, investigator -> deepseek

Three price classes collapse into one flat rate:

* **two vendors.** `azure_openai` contributed nothing only because the council did not
  convene on that run; the slots are configured and a normal run uses both, at rates
  that are not each other's.
* **cache hit vs miss.** DeepSeek's published table prices a hit at **$0.006/M** against
  **$0.3/M** for a miss — *fifty times apart*.
* **peak vs off-peak**, which halves everything.

And the dominant term was unmeasurable: ``LLMUsage`` had no cached-token field and no
client read one, so 20,892 of 21,482 input tokens — **97%** — had no cache attribution at
all. A flat rate over that is not an approximation; its error is unbounded and invisible.

THE RULE THIS FILE DEFENDS
==========================
    Tokens are priced at the rate they were billed at, or the cost is None.

Every test below is a way of getting that wrong. `derive_cost` returning a number it
cannot justify is the failure — not returning None.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services.consumption import (
    ConsumptionUnits,
    ModelPrice,
    PriceBook,
    VendorUsage,
    derive_cost,
    merge_vendor_usage,
    price_book_from_settings,
)

pytestmark = pytest.mark.anyio


#: The rates configured in production, from DeepSeek's official pricing table.
DEEPSEEK = ModelPrice(
    usd_per_million_input=0.3,
    usd_per_million_cached_input=0.006,
    usd_per_million_output=1.2,
)
AZURE = ModelPrice(
    usd_per_million_input=0.4,
    usd_per_million_cached_input=0.1,
    usd_per_million_output=1.6,
)


def _book(**vendors: ModelPrice) -> PriceBook:
    return PriceBook(vendor_rates=tuple(vendors.items()))


# --------------------------------------------------------------------------- #
# 1. THE 50x PROBLEM — an unreported cache split cannot be priced
#
# Catches: reading "no cache information" as "no cache hits", which bills every prompt
# token at the miss rate.
# --------------------------------------------------------------------------- #


class TestAnUnreportedCacheSplitIsUnpriceable:
    def test_cache_unreported_yields_no_cost_at_all(self) -> None:
        """The production state before this slice: 97% of input, unattributed."""
        units = ConsumptionUnits(
            by_vendor=(VendorUsage("deepseek", 6, 20_892, 0, 7_137, False),)
        )

        cost = derive_cost(units, _book(deepseek=DEEPSEEK))

        assert cost.estimated_usd is None, (
            "input tokens with no cache split were priced anyway — at $0.3/M they cost "
            "50x what they would at $0.006/M, and nothing here knows which"
        )
        assert any("cache_unreported" in u for u in cost.unpriced_units)

    def test_zero_cached_tokens_reported_is_a_real_measurement(self) -> None:
        """A measured zero IS priceable. Absent and zero are different facts."""
        units = ConsumptionUnits(
            by_vendor=(VendorUsage("deepseek", 1, 1_000, 0, 0, True),)
        )

        cost = derive_cost(units, _book(deepseek=DEEPSEEK))

        assert cost.estimated_usd == pytest.approx(1_000 / 1e6 * 0.3)

    def test_output_is_still_priced_when_input_is_not(self) -> None:
        """Output has one rate and is always priceable — but a partial total is None."""
        units = ConsumptionUnits(
            by_vendor=(VendorUsage("deepseek", 1, 500, 0, 200, False),)
        )

        cost = derive_cost(units, _book(deepseek=DEEPSEEK))

        assert cost.estimated_usd is None, "a subtotal was returned as a total"


# --------------------------------------------------------------------------- #
# 2. TWO VENDORS ARE NOT ONE RATE
#
# Catches: pricing every vendor's tokens at whichever rate happens to be configured.
# --------------------------------------------------------------------------- #


class TestVendorsArePricedSeparately:
    def test_each_vendor_is_billed_at_its_own_rates(self) -> None:
        units = ConsumptionUnits(
            by_vendor=(
                VendorUsage("deepseek", 1, 1_000, 400, 500, True),
                VendorUsage("azure_openai", 1, 2_000, 0, 300, True),
            )
        )

        cost = derive_cost(units, _book(deepseek=DEEPSEEK, azure_openai=AZURE))

        expected = (
            600 / 1e6 * 0.3
            + 400 / 1e6 * 0.006
            + 500 / 1e6 * 1.2
            + 2_000 / 1e6 * 0.4
            + 300 / 1e6 * 1.6
        )
        # Rounded to six decimals, which is the precision `derive_cost` stores. A
        # research run costs cents; a micro-dollar is below the noise of the token
        # counts themselves.
        assert cost.estimated_usd == pytest.approx(round(expected, 6))
        # And emphatically NOT the flat-rate answer over the sum.
        flat = 3_000 / 1e6 * 0.3 + 800 / 1e6 * 1.2
        assert cost.estimated_usd != pytest.approx(flat)

    def test_an_unconfigured_vendor_makes_the_whole_total_unknown(self) -> None:
        """Half a bill is not a bill. A cap compared against it passes on real spend."""
        units = ConsumptionUnits(
            by_vendor=(
                VendorUsage("deepseek", 1, 1_000, 0, 500, True),
                VendorUsage("azure_openai", 1, 2_000, 0, 300, True),
            )
        )

        cost = derive_cost(units, _book(deepseek=DEEPSEEK))

        assert cost.estimated_usd is None
        assert any("azure_openai" in u for u in cost.unpriced_units)

    def test_an_unattributed_leg_makes_the_total_unknown(self) -> None:
        """`unknown` is what a tool that named no vendor produces, and no book prices it."""
        units = ConsumptionUnits(
            by_vendor=(
                VendorUsage("deepseek", 1, 1_000, 0, 500, True),
                VendorUsage("unknown", 1, 100, 0, 50, True),
            )
        )

        assert derive_cost(units, _book(deepseek=DEEPSEEK)).estimated_usd is None

    def test_vendor_lookup_is_case_insensitive_but_never_fuzzy(self) -> None:
        book = _book(deepseek=DEEPSEEK)

        assert book.for_vendor("DeepSeek") is DEEPSEEK
        assert book.for_vendor(" deepseek ") is DEEPSEEK
        # A near-match must NOT resolve: applying one vendor's rate to another is the
        # exact failure this type exists to prevent.
        assert book.for_vendor("deepseek-v4") is None
        assert book.for_vendor("deep") is None


# --------------------------------------------------------------------------- #
# 3. A PARTIAL TOTAL IS NOT A TOTAL
#
# Catches: the pre-V3.17.9.2 behaviour — return what could be priced, list the rest.
# --------------------------------------------------------------------------- #


class TestPartialIsNeverTotal:
    def test_a_missing_output_rate_makes_the_total_unknown(self) -> None:
        units = ConsumptionUnits(
            by_vendor=(VendorUsage("deepseek", 1, 1_000, 0, 500, True),)
        )
        half = ModelPrice(usd_per_million_input=0.3, usd_per_million_cached_input=0.006)

        cost = derive_cost(units, _book(deepseek=half))

        assert cost.estimated_usd is None
        assert cost.unpriced_units

    def test_an_unpriced_search_call_makes_the_total_unknown(self) -> None:
        """Not only model tokens. Any measured unit without a rate blocks the total."""
        units = ConsumptionUnits(
            web_search_calls=10,
            by_vendor=(VendorUsage("deepseek", 1, 1_000, 0, 500, True),),
        )

        assert derive_cost(units, _book(deepseek=DEEPSEEK)).estimated_usd is None

    def test_an_empty_book_prices_nothing(self) -> None:
        units = ConsumptionUnits(
            by_vendor=(VendorUsage("deepseek", 1, 1_000, 0, 500, True),)
        )

        assert derive_cost(units, PriceBook()).estimated_usd is None

    def test_the_legacy_flat_path_still_works_without_a_breakdown(self) -> None:
        """A record written before this slice has no `by_vendor` and must still price.

        Correct only for a single-vendor, cache-free record — which is what those
        records are — and the flat rates stay for exactly that reason.
        """
        units = ConsumptionUnits(model_input_tokens=1_000, model_output_tokens=500)
        flat = PriceBook(
            usd_per_million_input_tokens=0.3, usd_per_million_output_tokens=1.2
        )

        cost = derive_cost(units, flat)

        assert cost.estimated_usd == pytest.approx(1_000 / 1e6 * 0.3 + 500 / 1e6 * 1.2)


# --------------------------------------------------------------------------- #
# 4. ATTRIBUTION SURVIVES MERGING AND PERSISTENCE
# --------------------------------------------------------------------------- #


class TestAttributionSurvives:
    def test_cache_reported_is_and_never_or(self) -> None:
        """One unreported call makes the SUM unreported. An OR would launder it."""
        merged = merge_vendor_usage(
            (VendorUsage("deepseek", 1, 100, 40, 10, True),),
            (VendorUsage("deepseek", 1, 100, 0, 10, False),),
        )

        assert len(merged) == 1
        assert merged[0].input_tokens == 200
        assert merged[0].cache_reported is False

    def test_merging_is_keyed_by_vendor(self) -> None:
        merged = merge_vendor_usage(
            (VendorUsage("deepseek", 1, 100, 0, 10, True),),
            (VendorUsage("azure_openai", 1, 50, 0, 5, True),),
        )

        assert [v.vendor for v in merged] == ["azure_openai", "deepseek"]

    def test_adding_units_merges_their_breakdowns(self) -> None:
        a = ConsumptionUnits(
            model_input_tokens=100,
            by_vendor=(VendorUsage("deepseek", 1, 100, 10, 5, True),),
        )
        b = ConsumptionUnits(
            model_input_tokens=50,
            by_vendor=(VendorUsage("deepseek", 1, 50, 5, 2, True),),
        )

        merged = a + b

        assert merged.model_input_tokens == 150
        assert merged.by_vendor[0].input_tokens == 150
        assert merged.by_vendor[0].cached_input_tokens == 15

    def test_the_breakdown_round_trips_through_json(self) -> None:
        """It is persisted in `consumption_json` and read back by the cost derivation."""
        units = ConsumptionUnits(
            model_input_tokens=100,
            by_vendor=(VendorUsage("deepseek", 2, 100, 40, 20, True),),
        )

        assert ConsumptionUnits.from_dict(units.to_dict()).by_vendor == units.by_vendor

    def test_uncached_is_derived_never_negative(self) -> None:
        """A provider reporting more cached than prompt tokens must not invert the sum."""
        assert VendorUsage("deepseek", 1, 10, 99, 0, True).uncached_input_tokens == 0


# --------------------------------------------------------------------------- #
# 5. THE PRICE BOOK IS CONFIGURATION, AND A BAD ONE IS UNKNOWN — NOT WRONG
# --------------------------------------------------------------------------- #


class TestThePriceBookIsConfiguration:
    def test_rates_are_read_from_one_setting(self) -> None:
        cfg = Settings(
            v3_price_vendor_rates=(
                '{"deepseek": {"input_per_million": 0.3, '
                '"cached_input_per_million": 0.006, "output_per_million": 1.2}}'
            )
        )

        rate = price_book_from_settings(cfg).for_vendor("deepseek")

        assert rate is not None and rate.is_complete
        assert rate.usd_per_million_input == 0.3
        assert rate.usd_per_million_cached_input == 0.006
        assert rate.usd_per_million_output == 1.2

    @pytest.mark.parametrize(
        "raw", ["{nope", "", "   ", "[]", '"a string"', "null", "123"]
    )
    def test_a_malformed_book_yields_no_rates_rather_than_raising(self, raw) -> None:  # noqa: ANN001
        """A typo in a setting must not end every research run, and must not guess."""
        book = price_book_from_settings(Settings(v3_price_vendor_rates=raw))

        assert book.vendor_rates == ()

    def test_a_zero_rate_is_a_price_and_a_negative_one_is_not(self) -> None:
        """A free tier is a fact. A negative rate is a typo and must not pay us back."""
        cfg = Settings(
            v3_price_vendor_rates=(
                '{"free": {"input_per_million": 0, "cached_input_per_million": 0, '
                '"output_per_million": 0}, "bad": {"input_per_million": -1}}'
            )
        )
        book = price_book_from_settings(cfg)

        free = book.for_vendor("free")
        assert free is not None and free.is_complete
        assert derive_cost(
            ConsumptionUnits(by_vendor=(VendorUsage("free", 1, 1_000, 0, 500, True),)),
            book,
        ).estimated_usd == pytest.approx(0.0)

        bad = book.for_vendor("bad")
        assert bad is not None and bad.usd_per_million_input is None

    def test_both_cost_paths_read_the_same_settings(self) -> None:
        """THE TWO-FAMILY DEFECT.

        `v3_pipeline._consumption` read `v3_price_usd_per_million_*` while
        `consumption_recorder` read `v3_price_per_million_*` — two setting families one
        word apart, for the same number. Configuring either produced a cost in one place
        and silence in the other, which is how a price book can look configured and
        leave the money record unpriced.
        """
        import inspect

        from app.services import consumption_recorder
        from app.services.pipeline import v3_pipeline

        for module in (v3_pipeline, consumption_recorder):
            source = inspect.getsource(module)
            assert "price_book_from_settings" in source, (
                f"{module.__name__} builds its own price book instead of reading the "
                "one function that knows where prices live"
            )


# --------------------------------------------------------------------------- #
# 6. THE PRODUCER — every client that spends tokens must report its cache split
#
# Found in PRODUCTION, not in review: with the price book configured and correct, the
# run still reported `model_input_tokens[deepseek:cache_unreported]` and a cost of None,
# because `DeepSeekModelProvider` accumulated prompt and completion tokens and dropped
# the cache split its own transport had already parsed. The rule caught it exactly.
# --------------------------------------------------------------------------- #


class TestTheDeepSeekModelProviderReportsItsCacheSplit:
    def _response(self, *, cached: int, reported: bool):  # noqa: ANN202
        from app.integrations.deepseek.transport import DeepSeekResponse

        return DeepSeekResponse(
            text="{}",
            prompt_tokens=1_000,
            completion_tokens=200,
            cached_tokens=cached,
            cache_reported=reported,
        )

    def _provider(self):  # noqa: ANN202
        from app.integrations.deepseek.providers import DeepSeekModelProvider

        return DeepSeekModelProvider(transport=object())  # type: ignore[arg-type]

    def test_a_reported_split_reaches_the_usage_record(self) -> None:
        provider = self._provider()
        provider._calls += 1
        provider._prompt_tokens += 1_000
        provider._completion_tokens += 200
        provider._cached_prompt_tokens += 400
        provider._cache_reported = True

        usage = provider.consume_usage()

        assert usage.cached_prompt_tokens == 400
        assert usage.cache_reported is True

    def test_the_usage_it_reports_is_priceable(self) -> None:
        """The end of the chain: a reported split makes the run cost a real number."""
        units = ConsumptionUnits(
            by_vendor=(VendorUsage("deepseek", 1, 1_000, 400, 200, True),)
        )

        cost = derive_cost(units, _book(deepseek=DEEPSEEK))

        assert cost.estimated_usd == pytest.approx(
            round(600 / 1e6 * 0.3 + 400 / 1e6 * 0.006 + 200 / 1e6 * 1.2, 6)
        )

    def test_consume_usage_resets_the_split_with_everything_else(self) -> None:
        """A second run must not inherit the first run's cached tokens."""
        provider = self._provider()
        provider._calls += 1
        provider._cached_prompt_tokens += 400
        provider._cache_reported = True
        provider.consume_usage()

        assert provider._cached_prompt_tokens == 0
        assert provider._cache_reported is None
        assert provider.consume_usage() is None

    def test_the_transport_reads_presence_not_value(self) -> None:
        """`cached_tokens: 0` from a body that never mentioned caching is not a zero."""
        from app.integrations.deepseek.transport import HttpDeepSeekTransport

        with_split = HttpDeepSeekTransport._reduce(
            {
                "choices": [{"message": {"content": "{}"}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "prompt_tokens_details": {"cached_tokens": 0},
                },
            }
        )
        silent = HttpDeepSeekTransport._reduce(
            {
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            }
        )

        assert with_split.cached_tokens == 0 and with_split.cache_reported is True
        assert silent.cached_tokens == 0 and silent.cache_reported is False
