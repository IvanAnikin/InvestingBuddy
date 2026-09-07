"""Canonical numeric reconciliation, server-side — V3.0 Slice 4.

WHY THIS FILE IS SHAPED THE WAY IT IS
=====================================
Every exclusion in the engine was learned from a real live report where the
guard suppressed CORRECT analysis. Each of those cases has a test here, named
after what it cost, because the failure mode of a numeric guard is not "it
misses a contradiction" — it is "it withholds nine correct statements and the
report becomes unreadable".

MRNA is the counterweight and it has its own test: 8 genuine conflicts, and a
guard that reports zero on MRNA is broken, not safe. A test suite that only
proved the engine stays quiet would pass on an engine that does nothing.
"""

from __future__ import annotations

import pytest

from app.services import numeric_verification as nv
from app.services.sources.fact_scope import GROUP_SCOPE_LABELS


def _snapshot(**slots) -> dict:
    return {"financial_snapshot": slots}


def _dp(value, **kw) -> dict:
    return {"numeric_value": value, **kw}


def _series(metric, points, **kw) -> dict:
    return {
        "historical_trends": {
            "series": [
                {
                    "metric": metric,
                    "periods": [
                        {"period": p, "value": v} for p, v in points
                    ],
                    **kw,
                }
            ]
        }
    }


def _index(content) -> nv.CanonicalIndex:
    return nv.build_canonical_index(content)


class TestOneVocabularyForScope:
    def test_group_labels_come_from_the_backend_not_a_copy(self):
        """The TS guard hand-maintained a duplicate of this set.

        Two copies of "what counts as the consolidated entity" is a
        desynchronisation waiting to happen: a fact persisted as Group could be
        read as a segment and stop adjudicating group claims. The server engine
        imports the one table.
        """
        for label in GROUP_SCOPE_LABELS:
            assert nv.scope_key_of(label) == nv.GROUP_SCOPE_KEY

    def test_a_segment_gets_its_own_key(self):
        assert nv.scope_key_of("Specialist Watchmakers") == (
            "segment:specialist watchmakers"
        )

    def test_unknown_scope_is_not_group(self):
        """UNKNOWN is a real answer, deliberately not a synonym for Group."""
        assert nv.scope_key_of(None) is None
        assert nv.scope_key_of("") is None
        assert nv.scope_key_of("   ") is None


class TestTheCanonicalIndex:
    def test_snapshot_slots_are_group_by_the_reports_own_convention(self):
        index = _index(
            _snapshot(revenue_primary_filing=_dp(32549, scale="million", period="2025"))
        )
        figure = index.figures["revenue"][0]
        assert figure.scope_key == nv.GROUP_SCOPE_KEY
        assert figure.magnitude == pytest.approx(32.549e9)

    def test_an_explicit_segment_label_survives(self):
        index = _index(
            _snapshot(
                operating_profit_primary_filing=_dp(
                    107, scale="million", scope="Specialist Watchmakers"
                )
            )
        )
        assert index.figures["operating_profit"][0].scope_key == (
            "segment:specialist watchmakers"
        )

    def test_the_multi_year_series_is_in_the_index(self):
        """Without it, correct historical citations were called contradictions.

        A real Pandora run: 13 of 111 sentences flagged, all correct — they
        quoted a period only the series carried.
        """
        index = _index(
            _series(
                "net_debt",
                [("FY2021", 2882), ("FY2025", 13719)],
                unit="DKK million",
                scope_type="group",
            )
        )
        assert len(index.figures["net_debt"]) == 2
        assert all(f.scale == "million" for f in index.figures["net_debt"])

    def test_a_superseded_period_is_excluded(self):
        content = {
            "historical_trends": {
                "series": [
                    {
                        "metric": "revenue",
                        "unit": "DKK million",
                        "periods": [
                            {"period": "2024", "value": 100, "superseded": True},
                            {"period": "2024", "value": 110},
                        ],
                    }
                ]
            }
        }
        values = [f.value for f in _index(content).figures["revenue"]]
        assert values == [110]

    def test_percent_spellings_all_normalise(self):
        """A margin classified as an amount is not a missed check — it is a WRONG one.

        With the canonical margin read as an amount, the engine looked for a
        non-percent number near "operating margin", found the operating profit
        in the same sentence, and called a correct Group statement a
        contradiction. Nine were withheld that way on a live CFR report.
        """
        for spelling in ("%", "percent", "percentage", "pct"):
            index = _index(
                _snapshot(operating_margin_primary_filing=_dp(20.0, unit=spelling))
            )
            assert index.figures["operating_margin"][0].is_percent

    def test_segment_names_are_longest_first(self):
        content = {
            "historical_trends": {
                "series": [
                    {"metric": "revenue", "scope": "Watches", "scope_type": "segment",
                     "periods": [{"period": "2025", "value": 1}]},
                    {"metric": "revenue", "scope": "Specialist Watches",
                     "scope_type": "segment",
                     "periods": [{"period": "2025", "value": 2}]},
                ]
            }
        }
        names = [name for name, _ in _index(content).segment_names]
        assert names[0] == "Specialist Watches"

    def test_an_unknown_metric_is_ignored(self):
        assert "made_up_metric" not in _index(
            _snapshot(made_up_metric_primary_filing=_dp(1))
        ).figures


class TestItCatchesRealContradictions:
    def test_a_wrong_group_figure_conflicts(self):
        index = _index(
            _snapshot(
                revenue_primary_filing=_dp(
                    32549, scale="million", period="2025", currency="DKK"
                )
            )
        )
        verdict = nv.check_sentence(
            "Group revenue was DKK 41,000 million in 2025.", index
        )
        assert verdict.verdict == nv.VERDICT_CONFLICTING
        assert verdict.metric == "revenue"

    def test_a_segment_figure_stated_as_group_is_caught(self):
        """The precision the scope key BUYS, not just the suppression it avoids."""
        content = {
            "financial_snapshot": {
                "operating_profit_primary_filing": _dp(
                    4500, scale="million", period="2025", scope="Group"
                )
            },
            "historical_trends": {
                "series": [
                    {
                        "metric": "operating_profit",
                        "scope": "Specialist Watchmakers",
                        "scope_type": "segment",
                        "unit": "EUR million",
                        "periods": [{"period": "2025", "value": 107}],
                    }
                ]
            },
        }
        index = _index(content)
        verdict = nv.check_sentence(
            "Group operating profit was EUR 107 million in 2025.", index
        )
        assert verdict.verdict == nv.VERDICT_CONFLICTING

    def test_a_report_with_genuine_conflicts_reports_them(self):
        """The MRNA counterweight: reporting zero here would mean broken."""
        index = _index(
            _snapshot(
                revenue_primary_filing=_dp(6800, scale="million", period="2025"),
                net_income_primary_filing=_dp(-3600, scale="million", period="2025"),
            )
        )
        sentences = [
            "Revenue of 9,000 million in 2025 marked a return to growth.",
            "Net income of 1,200 million in 2025 was positive.",
        ]
        verdicts = [nv.check_sentence(s, index).verdict for s in sentences]
        assert verdicts == [nv.VERDICT_CONFLICTING, nv.VERDICT_CONFLICTING]


class TestItDoesNotSuppressCorrectAnalysis:
    """Each of these withheld a correct statement on a real live report."""

    def test_a_segment_claim_is_judged_against_that_segment(self):
        """CFR: 32 statements withheld, every one of them correct."""
        content = {
            "financial_snapshot": {
                "operating_profit_primary_filing": _dp(
                    4500, scale="million", period="2025", scope="Group"
                )
            },
            "historical_trends": {
                "series": [
                    {
                        "metric": "operating_profit",
                        "scope": "Specialist Watchmakers",
                        "scope_type": "segment",
                        "unit": "EUR million",
                        "periods": [{"period": "2025", "value": 107}],
                    }
                ]
            },
        }
        verdict = nv.check_sentence(
            "Specialist Watchmakers operating profit was EUR 107 million in 2025.",
            _index(content),
        )
        assert verdict.verdict == nv.VERDICT_CONSISTENT

    def test_a_multiple_is_not_a_level(self):
        """PNDORA: "net debt ~2.6x equity" was read as a net-debt level."""
        index = _index(
            _series(
                "net_debt", [("2025", 13719)], unit="DKK million", scope_type="group"
            )
        )
        verdict = nv.check_sentence(
            "Net debt sits at roughly 2.6x equity, which is manageable.", index
        )
        assert verdict.verdict != nv.VERDICT_CONFLICTING

    def test_a_margin_beside_an_amount_is_not_a_contradiction(self):
        """CFR: nine correct Group statements, including the chair's."""
        index = _index(
            _snapshot(
                operating_profit_primary_filing=_dp(
                    4500, scale="million", period="2026", scope="Group"
                ),
                operating_margin_primary_filing=_dp(
                    20.0, unit="percent", period="2026", scope="Group"
                ),
            )
        )
        verdict = nv.check_sentence(
            "Group operating profit was 4.5 billion with an operating margin "
            "of 20.0% in 2026.",
            index,
        )
        assert verdict.verdict != nv.VERDICT_CONFLICTING

    def test_a_growth_percentage_is_not_a_level(self):
        index = _index(
            _snapshot(net_debt_primary_filing=_dp(13719, scale="million", period="2025"))
        )
        verdict = nv.check_sentence("Net debt rose 376% over the period.", _index(
            _snapshot(net_debt_primary_filing=_dp(13719, scale="million", period="2025"))
        ))
        assert verdict.verdict != nv.VERDICT_CONFLICTING
        assert index is not None

    def test_another_metrics_figure_does_not_convict_this_one(self):
        """PNDORA: total assets of DKK 29.603bn tested as a revenue claim."""
        index = _index(
            _snapshot(
                total_assets_primary_filing=_dp(29603, scale="million", period="2025"),
                revenue_primary_filing=_dp(32549, scale="million", period="2025"),
            )
        )
        verdict = nv.check_sentence(
            "Total assets of 29.603 billion relative to revenue and equity "
            "suggest a capital-intensive business in 2025.",
            index,
        )
        assert verdict.verdict != nv.VERDICT_CONFLICTING

    def test_a_period_the_report_does_not_hold_is_not_adjudicated(self):
        """PNDORA: 10 of 11 false positives were exactly this."""
        index = _index(
            _snapshot(revenue_primary_filing=_dp(32549, scale="million", period="2025"))
        )
        verdict = nv.check_sentence(
            "Revenue was DKK 19,000 million in FY2019.", index
        )
        assert verdict.verdict == nv.VERDICT_UNCHECKED

    def test_a_bare_year_is_a_date_not_a_quantity(self):
        index = _index(
            _snapshot(revenue_primary_filing=_dp(32549, scale="million", period="2025"))
        )
        verdict = nv.check_sentence(
            "In 2025, revenue was DKK 32.5 billion.", index
        )
        assert verdict.verdict == nv.VERDICT_CONSISTENT

    def test_a_period_token_is_not_a_number(self):
        """Reading the 1 out of "H1" flagged a perfectly good sentence."""
        numbers = nv.prose_numbers("in h1 2026, revenue rose")
        assert all(n.raw != 1 for n in numbers)

    def test_a_trailing_separator_is_not_grouping(self):
        """"in H1 2026, revenue" captured "2026, " and read as a revenue of 2026."""
        assert [n.raw for n in nv.prose_numbers("in h1 2026, revenue was 32,549")] == [
            32549
        ]

    def test_a_different_currency_is_not_comparable(self):
        index = _index(
            _snapshot(
                revenue_primary_filing=_dp(
                    32549, scale="million", period="2025", currency="DKK"
                )
            )
        )
        verdict = nv.check_sentence(
            "Revenue was EUR 4,400 million in 2025.", index
        )
        assert verdict.verdict != nv.VERDICT_CONFLICTING

    def test_a_comparison_figure_beside_the_current_one_is_not_a_conflict(self):
        """Prose routinely carries last year's figure beside this year's."""
        index = _index(
            _series(
                "revenue",
                [("2025", 32549), ("2024", 31200)],
                unit="DKK million",
                scope_type="group",
            )
        )
        verdict = nv.check_sentence(
            "Revenue reached DKK 32,549 million against DKK 31,200 million "
            "a year earlier.",
            index,
        )
        assert verdict.verdict == nv.VERDICT_CONSISTENT

    def test_a_bare_magnitude_without_a_scale_word_is_not_matched(self):
        """Documented parity with the browser guard, not an oversight.

        A canonical value of 32,549 MILLION and a prose "32,549" are the same
        number written at different scales, and nothing in the sentence says
        which. The browser guard has always required the scale word, and this
        engine matches it exactly — a server that adjudicated more permissively
        than the guard beside it would produce two different answers for one
        report, which is the disagreement this slice exists to remove.
        """
        index = _index(
            _series("revenue", [("2025", 32549)], unit="DKK million", scope_type="group")
        )
        assert nv.check_sentence("Revenue reached 32,549.", index).verdict == (
            nv.VERDICT_CONFLICTING
        )

    def test_a_distant_metric_name_does_not_capture_a_number(self):
        index = _index(
            _snapshot(
                operating_profit_primary_filing=_dp(4500, scale="million", period="2025")
            )
        )
        verdict = nv.check_sentence(
            "Net debt of 13,719 million against equity of 5,282 million leaves "
            "little headroom, and the picture would worsen materially if "
            "operating profit fell.",
            index,
        )
        assert verdict.verdict != nv.VERDICT_CONFLICTING

    def test_a_sentence_with_no_metric_is_unchecked(self):
        index = _index(
            _snapshot(revenue_primary_filing=_dp(32549, scale="million"))
        )
        assert nv.check_sentence(
            "Management guided to continued expansion in 2026.", index
        ).verdict == nv.VERDICT_UNCHECKED

    def test_an_empty_index_adjudicates_nothing(self):
        assert nv.check_sentence(
            "Revenue was DKK 41,000 million.", nv.EMPTY_INDEX
        ).verdict == nv.VERDICT_UNCHECKED

    def test_an_empty_sentence_is_unchecked(self):
        index = _index(_snapshot(revenue_primary_filing=_dp(1000)))
        assert nv.check_sentence("", index).verdict == nv.VERDICT_UNCHECKED
        assert nv.check_sentence("   ", index).verdict == nv.VERDICT_UNCHECKED


class TestWholeReportVerification:
    def _report(self, *, claims: list[str]) -> dict:
        return {
            "financial_snapshot": {
                "revenue_primary_filing": _dp(
                    32549, scale="million", period="2025", currency="DKK"
                )
            },
            "llm_council_analysis": {
                "agents": [
                    {
                        "agent_name": "financial_analyst",
                        "key_points": [{"claim": c} for c in claims],
                    }
                ]
            },
        }

    def test_it_records_a_conflict_against_the_statement(self):
        record = nv.verify_report_content(
            self._report(claims=["Revenue was DKK 41,000 million in 2025."])
        )
        assert record["conflicting"] == 1
        conflict = record["conflicts"][0]
        assert conflict["metric"] == "revenue"
        assert conflict["statement"] == "Revenue was DKK 41,000 million in 2025."
        assert conflict["path"].startswith("agents.financial_analyst.key_points.")

    def test_it_never_edits_the_prose(self):
        """A withheld statement that has been overwritten cannot be reviewed."""
        content = self._report(claims=["Revenue was DKK 41,000 million in 2025."])
        original = content["llm_council_analysis"]["agents"][0]["key_points"][0][
            "claim"
        ]
        nv.verify_report_content(content)
        assert (
            content["llm_council_analysis"]["agents"][0]["key_points"][0]["claim"]
            == original
        )

    def test_the_counts_name_their_population(self):
        record = nv.verify_report_content(
            self._report(
                claims=[
                    "Revenue was DKK 32,549 million in 2025.",
                    "Revenue was DKK 41,000 million in 2025.",
                    "Management remains confident.",
                ]
            )
        )
        assert record["statements_examined"] == 3
        assert record["consistent"] == 1
        assert record["conflicting"] == 1
        assert record["unchecked"] == 1
        assert record["canonical_metrics"] == 1
        assert record["canonical_figures"] == 1

    def test_an_implication_is_adjudicated_on_statement_plus_mechanism(self):
        """The number often sits in one field and the metric name in the other."""
        content = {
            "financial_snapshot": {
                "revenue_primary_filing": _dp(32549, scale="million", period="2025")
            },
            "llm_council_analysis": {
                "agents": [
                    {
                        "agent_name": "chair",
                        "implications": [
                            {
                                "statement": "Revenue scale supports operating leverage",
                                "mechanism": "at 41,000 million in 2025 the fixed base is spread wider",
                            }
                        ],
                    }
                ]
            },
        }
        record = nv.verify_report_content(content)
        assert record["conflicting"] == 1
        # Reported as the STATEMENT — the field a reader sees withheld.
        assert (
            record["conflicts"][0]["statement"]
            == "Revenue scale supports operating leverage"
        )

    def test_an_empty_report_is_handled(self):
        record = nv.verify_report_content({})
        assert record["statements_examined"] == 0
        assert record["conflicts"] == []
        assert record["engine_version"] == nv.NUMERIC_VERIFICATION_VERSION

    def test_none_content_is_handled(self):
        assert nv.verify_report_content(None)["conflicting"] == 0

    def test_the_record_carries_the_engine_version(self):
        """A persisted verdict is never assumed compatible with current code."""
        record = nv.verify_report_content({})
        assert record["engine_version"] == nv.NUMERIC_VERIFICATION_VERSION
        assert isinstance(nv.NUMERIC_VERIFICATION_VERSION, int)

    def test_the_notice_is_the_one_the_frontend_renders(self):
        assert nv.CONFLICT_NOTICE == (
            "Conflicting evidence — technical review required."
        )


class TestItIsWiredIntoAssembly:
    def test_the_generator_writes_the_section(self):
        import inspect

        from app.services import final_report_generator

        source = inspect.getsource(
            final_report_generator.FinalReportGeneratorService._generate_and_save
        )
        assert "numeric_verification.verify_report_content(report_content)" in source
        # Written BEFORE validation, so the safety gate scans the record too.
        assert source.index("numeric_verification.verify_report_content") < source.index(
            "validation = run_final_report_validation("
        )
        # And before the save, so it is part of what is persisted.
        assert source.index("numeric_verification.verify_report_content") < source.index(
            "saved_report = await _save_final_report_draft("
        )
