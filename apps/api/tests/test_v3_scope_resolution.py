"""Layered scope resolution over real document structure. V3.11 slice 11.2.

Every fixture here is a real structural pattern taken from the Richemont FY26 annual
report used in the V3.10 acceptance run — the sentences are verbatim — but NOTHING in the
implementation or in these tests keys off the issuer's name. The segment names arrive by
being *disclosed in the document*, which is what makes the mechanism generic: another
issuer's report teaches the resolver its own segments the same way.

The property under test is not coverage. It is:

    **no layer may promote a segment figure to Group scope.**
"""

from __future__ import annotations

import pytest

from app.services.corpus.scope_resolution import (
    AMBIGUITY_CLASSIFIER_UNSAFE,
    AMBIGUITY_MULTIPLE_SEGMENTS,
    AMBIGUITY_NO_SIGNAL,
    AMBIGUITY_SEGMENT_AND_GROUP,
    METHOD_CLASSIFIER,
    METHOD_EXPLICIT,
    METHOD_HEADING,
    METHOD_ROW_LABEL,
    METHOD_TABLE_CAPTION,
    METHOD_VOCABULARY,
    STRENGTH_MEASURE,
    SegmentVocabulary,
    group_claim_signal,
    learn_segment_vocabulary,
    resolve_scope,
)
from app.services.sources.fact_scope import GROUP_SCOPE, SCOPE_TYPE_SEGMENT, FactScope

# The real "sales by business area" disclosure. This one block is what teaches the
# resolver this issuer's three reporting segments.
HIGHLIGHTS = (
    "Sales by business area (% of Group) 2026 Specialist Watchmakers Other Jewellery "
    "Maisons 14% 74% 12% Jewellery Maisons (€m) 2026 16 539 2025 15 328 Specialist "
    "Watchmakers (€m) 2026 3 149 2025 3 283 Other Businesses (€m) 2026 2 732 2025 2 788 "
    "• Group sales at € 22.4 billion, up by 11% at constant rates"
)

# The mandatory regression sentence, verbatim, including the € 107 million figure.
WATCHMAKERS = (
    "The Group’s Specialist Watchmakers reported sales of € 3.1 billion, down by 4% at "
    "actual exchange rates, but up modestly at constant rates, showing some encouraging "
    "signs after a challenging 24-month period for the watch market, underpinned by "
    "growth outside of China. The operating result came in at € 107 million, with gross "
    "margin impacted by external macroeconomic headwinds. On 22 January 2026, Richemont "
    "and the Damiani Group, a prestigious, family-run Italian global luxury group, "
    "announced that we had signed an agreement for the Damiani Group to acquire full "
    "ownership of specialist watchmaker Baume & Mercier from Richemont."
)


@pytest.fixture
def vocab() -> SegmentVocabulary:
    return learn_segment_vocabulary(section_texts=[("Financial highlights", HIGHLIGHTS)])


class TestVocabularyIsLearnedFromTheDocument:
    def test_the_document_teaches_its_own_segments(self, vocab) -> None:
        assert set(vocab.names) == {
            "Jewellery Maisons",
            "Specialist Watchmakers",
            "Other Businesses",
        }

    def test_each_name_records_where_it_was_learned(self, vocab) -> None:
        """Provenance, so a reviewer can tell a learned name from a hardcoded one."""
        for name in vocab.names:
            assert "by business area" in vocab.sources[name]

    def test_prose_outside_a_segment_disclosure_teaches_nothing(self) -> None:
        """A name in an ordinary paragraph is a company, a person or a product."""
        learned = learn_segment_vocabulary(
            section_texts=[("Chairman’s review", "We acquired Baume & Mercier (€m) 2026 120.")]
        )
        assert learned.names == ()

    def test_a_financial_line_item_is_not_a_segment(self) -> None:
        learned = learn_segment_vocabulary(
            section_texts=[
                (
                    "Segment information",
                    "Operating Profit (€m) 2026 4 500 Total Sales (€m) 2026 22 400",
                )
            ]
        )
        assert learned.names == ()

    def test_table_rows_teach_segments_when_the_caption_says_they_do(self) -> None:
        learned = learn_segment_vocabulary(
            table_rows=[
                (
                    "Segment information",
                    [["Jewellery Maisons", "16539"], ["Specialist Watchmakers", "3149"]],
                )
            ]
        )
        assert "Specialist Watchmakers" in learned

    def test_a_table_without_a_segment_caption_teaches_nothing(self) -> None:
        learned = learn_segment_vocabulary(
            table_rows=[("Directors’ remuneration", [["J. Rupert", "1200"]])]
        )
        assert learned.names == ()


class TestGroupClaimDetection:
    """Every rejection below is a phrase that occurs in the real corpus."""

    @pytest.mark.parametrize(
        "text",
        [
            "Richemont and the Damiani Group announced an agreement",
            "best realised as part of the Damiani Group",
            "a prestigious, family-run Italian global luxury group",
            "peer group benchmarking places the company mid-table",
            "the disposal group was written down in the prior year",
            "assets held for sale of € 82 million",
        ],
    )
    def test_these_are_not_the_reporting_entity(self, text) -> None:
        assert group_claim_signal(text) is None

    @pytest.mark.parametrize(
        "text",
        [
            "Group sales at € 22.4 billion",
            "the Group maintained a strong net cash position",
            "At Group level, operating profit came in at € 4.5 billion",
        ],
    )
    def test_these_are(self, text) -> None:
        assert group_claim_signal(text) is not None

    def test_at_group_level_survives_the_third_party_filter(self) -> None:
        """ "At" is capitalised at the start of a sentence. Before this was fixed, the
        third-party-company rule read "At Group" as a corporate name and deleted the
        strongest Group signal in the chunk."""
        assert group_claim_signal("At Group level, operating profit was € 4.5 billion")

    def test_the_possessive_attributes_to_the_segment(self, vocab) -> None:
        """ "The Group's Specialist Watchmakers" is a SEGMENT reference. The Group owns
        the segment; the figure belongs to the segment."""
        assert group_claim_signal(WATCHMAKERS, segments=vocab.names) is None

    def test_measure_strength_ignores_a_passing_mention(self) -> None:
        """Asserting Group scope from prose needs a measure, not a mention. A paragraph
        about a Maison's creative director mentions the Group and is not a Group figure.
        """
        passing = "In 2024, the Group appointed a new creative director for the Maison."
        assert group_claim_signal(passing) is not None
        assert group_claim_signal(passing, strength=STRENGTH_MEASURE) is None

    def test_measure_strength_accepts_a_real_group_figure(self) -> None:
        assert group_claim_signal("Group sales reached € 22.4 billion", strength=STRENGTH_MEASURE)

    def test_a_reference_to_the_accounts_is_not_a_measure(self) -> None:
        """ "Consolidated financial statements" names a document, not a figure."""
        text = "Refer to the consolidated financial statements on page 96."
        assert group_claim_signal(text, strength=STRENGTH_MEASURE) is None


class TestTheMandatoryCfrRegression:
    """A Specialist Watchmakers figure must never be promoted to Group scope."""

    def test_the_watchmakers_figure_resolves_to_its_segment(self, vocab) -> None:
        result = resolve_scope(text=WATCHMAKERS, vocabulary=vocab)
        assert result.scope.scope_key == "segment:specialist watchmakers"
        assert result.method == METHOD_VOCABULARY

    def test_the_107m_operating_result_is_inside_that_very_chunk(self, vocab) -> None:
        """The € 107 million case, resolved by structure rather than by a special rule."""
        assert "€ 107 million" in WATCHMAKERS
        assert resolve_scope(text=WATCHMAKERS, vocabulary=vocab).scope.is_segment

    def test_it_is_never_group_even_though_the_sentence_says_group(self, vocab) -> None:
        assert resolve_scope(text=WATCHMAKERS, vocabulary=vocab).scope.is_group is False

    def test_a_third_party_group_in_the_same_chunk_does_not_make_it_group(self, vocab) -> None:
        """ "the Damiani Group" appears twice in the real sentence."""
        assert WATCHMAKERS.count("Damiani Group") == 2
        assert resolve_scope(text=WATCHMAKERS, vocabulary=vocab).scope.scope_type == (
            SCOPE_TYPE_SEGMENT
        )

    def test_jewellery_maisons_stays_a_different_series(self, vocab) -> None:
        jewellery = "Jewellery Maisons increased sales by 11% to € 16 539 million, led by Cartier."
        result = resolve_scope(text=jewellery, vocabulary=vocab)
        assert result.scope.scope_key == "segment:jewellery maisons"
        assert result.scope.scope_key != "segment:specialist watchmakers"

    def test_a_group_figure_still_resolves_to_group(self, vocab) -> None:
        result = resolve_scope(text="Group sales reached € 22.4 billion.", vocabulary=vocab)
        assert result.scope.is_group

    def test_a_chunk_holding_both_a_breakdown_and_a_total_stays_unknown(self, vocab) -> None:
        """The real highlights page carries three segment figures AND the Group total.
        It cannot be cited as either, so it is cited as neither."""
        result = resolve_scope(text=HIGHLIGHTS, vocabulary=vocab)
        assert result.scope.is_unknown
        assert result.ambiguity == AMBIGUITY_MULTIPLE_SEGMENTS

    def test_a_segment_beside_a_group_measure_stays_unknown(self, vocab) -> None:
        mixed = (
            "the Jewellery Maisons were therefore able to grow. At Group level, operating "
            "profit came in at € 4.5 billion."
        )
        result = resolve_scope(text=mixed, vocabulary=vocab)
        assert result.scope.is_unknown
        assert result.ambiguity == AMBIGUITY_SEGMENT_AND_GROUP

    def test_unknown_stays_unknown(self, vocab) -> None:
        result = resolve_scope(text="The Board met four times during the year.", vocabulary=vocab)
        assert result.scope.is_unknown
        assert result.ambiguity == AMBIGUITY_NO_SIGNAL


class TestTheLayerOrder:
    def test_explicit_metadata_wins(self, vocab) -> None:
        result = resolve_scope(
            text="Jewellery Maisons sales were € 16 539 million.",
            explicit=GROUP_SCOPE,
            vocabulary=vocab,
        )
        assert result.method == METHOD_EXPLICIT
        assert result.scope.is_group

    def test_a_caption_beats_the_body(self, vocab) -> None:
        result = resolve_scope(
            text="2026 3 149 2025 3 283",
            table_caption="Specialist Watchmakers — sales by year",
            vocabulary=vocab,
        )
        assert result.method == METHOD_TABLE_CAPTION
        assert result.scope.scope_key == "segment:specialist watchmakers"

    def test_a_heading_naming_a_disclosed_segment_is_accepted(self, vocab) -> None:
        result = resolve_scope(
            text="Sales grew 11%.",
            heading_scope=FactScope(
                scope_type=SCOPE_TYPE_SEGMENT, scope_name="Specialist Watchmakers"
            ),
            vocabulary=vocab,
        )
        assert result.method == METHOD_HEADING

    def test_a_heading_naming_something_that_is_not_a_segment_is_refused(self, vocab) -> None:
        """The real defect this fixes: the old rule made any leaf heading under a
        "Sales by region" ancestor a segment, and produced `segment:proposed dividend`.
        A dividend is not a business segment, and the document never disclosed it as one.
        """
        result = resolve_scope(
            text="The Board proposes a dividend of CHF 3.30.",
            heading_scope=FactScope(scope_type=SCOPE_TYPE_SEGMENT, scope_name="Proposed dividend"),
            vocabulary=vocab,
        )
        assert result.scope.is_unknown
        assert "not a disclosed segment" in (result.evidence or "")

    def test_a_single_row_label_scopes_a_table(self, vocab) -> None:
        result = resolve_scope(
            text="rendered table",
            table_rows=[["Specialist Watchmakers", "3149", "3283"]],
            vocabulary=vocab,
        )
        assert result.method == METHOD_ROW_LABEL

    def test_a_breakdown_table_has_no_single_scope(self, vocab) -> None:
        result = resolve_scope(
            text="rendered table",
            table_rows=[
                ["Jewellery Maisons", "16539"],
                ["Specialist Watchmakers", "3149"],
            ],
            vocabulary=vocab,
        )
        assert result.scope.is_unknown
        assert result.ambiguity == AMBIGUITY_MULTIPLE_SEGMENTS

    def test_a_heading_only_scopes_the_chunk_that_follows_it(self, vocab) -> None:
        """Heading→text alignment on a two-column PDF is unreliable, so "nearby" has to
        mean nearby."""
        drifted = resolve_scope(
            text="Central functions comprise regional support and administration.",
            section_path="Business review > Specialist Watchmakers",
            heading_adjacent=False,
            vocabulary=vocab,
        )
        assert drifted.scope.is_unknown

    def test_even_an_adjacent_heading_needs_corroboration(self, vocab) -> None:
        result = resolve_scope(
            text="Central functions comprise regional support and administration.",
            section_path="Business review > Specialist Watchmakers",
            heading_adjacent=True,
            vocabulary=vocab,
        )
        assert result.scope.is_unknown


class TestTheClassifierCannotPromote:
    class _WantsGroup:
        def classify(self, text: str, candidates) -> str | None:  # noqa: ANN001
            return "Group"

    class _Hallucinates:
        def classify(self, text: str, candidates) -> str | None:  # noqa: ANN001
            return "Aerospace Division"

    class _Picks:
        def classify(self, text: str, candidates) -> str | None:  # noqa: ANN001
            return "Specialist Watchmakers"

    def test_a_model_cannot_answer_group(self, vocab) -> None:
        """Not "is discouraged from": the vocabulary is the only permitted answer, and
        "Group" is not in it."""
        result = resolve_scope(
            text="Sales were € 3.1 billion.",
            vocabulary=vocab,
            classifier=self._WantsGroup(),
        )
        assert result.scope.is_group is False
        assert result.ambiguity == AMBIGUITY_CLASSIFIER_UNSAFE

    def test_a_segment_the_document_never_disclosed_is_refused(self, vocab) -> None:
        result = resolve_scope(
            text="Sales were € 3.1 billion.",
            vocabulary=vocab,
            classifier=self._Hallucinates(),
        )
        assert result.scope.is_unknown
        assert result.evidence == "Aerospace Division"

    def test_it_may_choose_from_the_documents_own_vocabulary(self, vocab) -> None:
        result = resolve_scope(
            text="Sales were € 3.1 billion.",
            vocabulary=vocab,
            classifier=self._Picks(),
        )
        assert result.method == METHOD_CLASSIFIER
        assert result.scope.scope_key == "segment:specialist watchmakers"

    def test_it_is_never_consulted_before_the_deterministic_layers(self, vocab) -> None:
        result = resolve_scope(text=WATCHMAKERS, vocabulary=vocab, classifier=self._WantsGroup())
        assert result.method == METHOD_VOCABULARY


class TestProvenance:
    def test_every_resolution_explains_itself(self, vocab) -> None:
        payload = resolve_scope(text=WATCHMAKERS, vocabulary=vocab).as_dict()
        assert payload["method"] == METHOD_VOCABULARY
        assert payload["confidence"] > 0
        assert "Specialist Watchmakers" in str(payload["evidence"])
        assert payload["ambiguity"] is None

    def test_a_refusal_explains_itself_too(self, vocab) -> None:
        payload = resolve_scope(text=HIGHLIGHTS, vocabulary=vocab).as_dict()
        assert payload["scope_key"] is None
        assert payload["ambiguity"] == AMBIGUITY_MULTIPLE_SEGMENTS
        assert "Specialist Watchmakers" in str(payload["evidence"])

    def test_no_vocabulary_means_no_invention(self) -> None:
        """With nothing learned, the resolver falls back to the pre-existing behaviour
        rather than guessing."""
        result = resolve_scope(text=WATCHMAKERS, vocabulary=SegmentVocabulary())
        assert result.scope.is_unknown
