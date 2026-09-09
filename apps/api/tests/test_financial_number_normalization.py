"""The canonical numeric parser must read the forms filings actually print.

WHAT WENT WRONG IN PRODUCTION
=============================
The first live V3 run refused five real, SEC-sourced claims with:

    "The claimed value '1.0 billion' has no numeric reading at all, so it cannot
     be located in a source."

``$1.0 billion``, ``$3.2 billion`` and ``$(1.1) billion`` are ordinary financial prose.
The parser stripped a trailing alpha run of at most FOUR characters — so ``3.2bn``
survived and ``1.0 billion`` did not — and the strict pattern then rejected the whole
string. A financial verifier that cannot read "billion" is not fit for the job.

WHAT THIS FIX IS NOT
====================
It is not looser matching. A claim is now compared against every representation **of
itself** — the prose form and the same quantity written in a table of units, thousands,
millions or billions — and each still has to be found EXACTLY within the existing
precision window. Recognising that one quantity is written several ways is not the same
as widening the tolerance around a different quantity.
"""

from __future__ import annotations

import pytest

from app.services.providers.leads import (
    parse_number,
    parse_number_candidates,
    split_scale_suffix,
)


class TestTheFormsTheBriefRequires:
    @pytest.mark.parametrize(
        ("text", "magnitude"),
        [
            ("1.0 billion", 1_000_000_000.0),
            ("$1.0 billion", 1_000_000_000.0),
            ("1 billion", 1_000_000_000.0),
            ("3.2bn", 3_200_000_000.0),
            ("3.2 billion", 3_200_000_000.0),
            ("1,343 million", 1_343_000_000.0),
            ("$1,343 million", 1_343_000_000.0),
            ("145 million", 145_000_000.0),
            ("$389 million", 389_000_000.0),
        ],
    )
    def test_a_scaled_figure_yields_its_full_magnitude(
        self, text: str, magnitude: float
    ) -> None:
        assert magnitude in parse_number_candidates(text)

    @pytest.mark.parametrize(
        "text", ["$(1.1) billion", "($1.1 billion)", "-$1.1 billion", "(1.1) billion"]
    )
    def test_every_negative_form_is_negative(self, text: str) -> None:
        """Three notations for one loss, and the sign is the whole meaning. An earlier
        pass through this fix returned +1.1bn for two of them."""
        candidates = parse_number_candidates(text)
        assert candidates, f"{text!r} did not parse at all"
        assert all(c < 0 for c in candidates), f"{text!r} -> {candidates}"
        assert -1_100_000_000.0 in candidates

    def test_percentages_commas_and_currency_symbols(self) -> None:
        assert parse_number("12.5%") == 12.5
        assert parse_number("EUR 1,234.5") == 1234.5
        assert parse_number("1,234.5 DKK") == 1234.5
        assert parse_number("$389") == 389.0


class TestItStillRefusesToInvent:
    def test_a_period_is_not_a_number(self) -> None:
        """The rule this parser exists to keep: an earlier draft scraped digits and
        read 'Q1 2026' as 12026 — a number no document contains, handed to a
        comparison that would then have 'found' it."""
        assert parse_number_candidates("Q1 2026") == []
        assert parse_number_candidates("") == []
        assert parse_number_candidates("1,2x34.5") == []

    def test_a_currency_code_ending_in_a_scale_letter_is_not_a_scale(self) -> None:
        """`DKK` ends in K. Reading that as 'thousand' turned a Danish krone figure
        into a thousandfold overstatement — found by an existing test, not by me."""
        assert split_scale_suffix("1,234.5 DKK") == ("1,234.5 DKK", 1.0)
        assert parse_number_candidates("1,234.5 DKK") == [1234.5]
        assert parse_number_candidates("1234 SEK") == [1234.0]

    def test_ambiguity_is_still_preserved_not_resolved(self) -> None:
        """`1,234` is 1234 under US grouping and 1.234 under European decimals, and the
        string alone does not say which. Picking one is a 1000x error half the time."""
        assert sorted(parse_number_candidates("1,234")) == [1.234, 1234.0]
        assert parse_number("1,234") is None

    def test_a_scaled_claim_does_not_admit_a_sub_unit_reading(self) -> None:
        """'145 million' must not offer 0.145 as a candidate — no filing prints a
        $145m line as 0.145, and admitting it invites a match on an unrelated ratio."""
        assert 0.145 not in parse_number_candidates("145 million")


class TestScaleEquivalenceIsNotLooseness:
    def test_a_billions_claim_matches_the_same_figure_in_a_millions_table(self) -> None:
        """Prose says "$1.0 billion"; the table in the same filing says "1,000"."""
        candidates = parse_number_candidates("$1.0 billion")
        assert 1000.0 in candidates
        assert 1_000_000_000.0 in candidates

    def test_the_bare_mantissa_is_not_a_match_target(self) -> None:
        """`numbers_in` scans a WHOLE filing, so admitting 1.0 as a reading of
        "$1.0 billion" meant any occurrence of "1.0" anywhere verified the claim.
        Mantissas of one to nine are the most common numbers in a financial document,
        which made every scaled claim close to unfalsifiable. Found by review.

        This is not the gate getting looser or stricter about the QUANTITY — it is
        refusing a reading with too few significant digits to identify anything."""
        assert 1.0 not in parse_number_candidates("$1.0 billion")
        assert 3.2 not in parse_number_candidates("3.2 billion")
        assert -1.1 not in parse_number_candidates("$(1.1) billion")
        # An unscaled figure is untouched: it is what the document literally says.
        assert parse_number_candidates("145") == [145.0]
        assert parse_number_candidates("12.5%") == [12.5]

    def test_it_does_not_admit_a_different_quantity(self) -> None:
        """Every candidate is the SAME quantity at another scale. 1.1 is not among
        the readings of 1.0 billion, at any scale."""
        assert 1.1 not in parse_number_candidates("$1.0 billion")
        assert 2.0 not in parse_number_candidates("$1.0 billion")
