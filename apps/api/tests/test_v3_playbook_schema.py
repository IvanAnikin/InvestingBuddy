"""V3.6 Slice 6.1 — the playbook schema and selection.

> **Industry methodology is versioned configuration, not a prompt.**

These tests are mostly constructor refusals, because that is where a typed declaration
earns its keep over a YAML file: a playbook that would silently be unable to answer its
own mandatory question is **unrepresentable** rather than merely invalid.
"""

from __future__ import annotations

import pytest

from app.services.playbooks import registry
from app.services.playbooks.schema import (
    EVALUABLE_COMPLETION_RULES,
    AppliesTo,
    Playbook,
    PlaybookQuestion,
)


def _question(**overrides) -> PlaybookQuestion:
    base = {
        "key": "q1",
        "text": "a question",
        "required_tools": frozenset({"get_financial_facts"}),
    }
    base.update(overrides)
    return PlaybookQuestion(**base)


def _playbook(**overrides) -> Playbook:
    base = {
        "playbook_id": "test",
        "version": 1,
        "display_name": "Test",
        "applies_to": AppliesTo(sectors=("Industrials",)),
        "questions": (_question(),),
        "risk_framework": ("execution",),
    }
    base.update(overrides)
    return Playbook(**base)


class TestQuestionRefusals:
    def test_a_tool_that_does_not_exist_is_refused(self) -> None:
        """A question requiring a nonexistent tool would look like a research gap
        forever."""
        with pytest.raises(ValueError, match="research gap forever"):
            _question(required_tools=frozenset({"read_the_internet"}))

    def test_a_calculation_the_engine_cannot_produce_is_refused(self) -> None:
        with pytest.raises(ValueError, match="nothing can compute"):
            _question(required_calculations=("vibes_per_share",))

    def test_an_evidence_class_that_is_not_an_access_class_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not access classes"):
            _question(required_evidence_classes=("trade_press",))

    def test_a_blocking_question_naming_no_tools_is_refused(self) -> None:
        """It would stop every run of the playbook, and the symptom would look like a
        coverage problem rather than a declaration error."""
        with pytest.raises(ValueError, match="stops every run"):
            _question(required_tools=frozenset(), blocking=True)

    def test_a_real_calculation_is_accepted(self) -> None:
        from app.services.calculations.definitions import DEFINITIONS

        key = sorted(DEFINITIONS)[0]
        assert _question(required_calculations=(key,)).required_calculations == (key,)


class TestPlaybookRefusals:
    def test_a_playbook_with_no_questions_is_refused(self) -> None:
        """Changing nothing about the methodology is the only thing it is for."""
        with pytest.raises(ValueError, match="only thing it is for"):
            _playbook(questions=())

    def test_a_playbook_with_no_risk_taxonomy_is_refused(self) -> None:
        """Otherwise "we found no risks" can mean "nobody looked for any"."""
        with pytest.raises(ValueError, match="nobody looked"):
            _playbook(risk_framework=())

    def test_a_specialist_nobody_implements_is_refused(self) -> None:
        with pytest.raises(ValueError, match="silently missing perspective"):
            _playbook(specialist_roles=("astrologer",))

    def test_a_completion_rule_the_loop_cannot_evaluate_is_refused(self) -> None:
        """An unevaluable rule never declares a run complete, so the playbook would
        never finish — and the symptom would be a budget exhaustion."""
        with pytest.raises(ValueError, match="budget exhaustion"):
            _playbook(completion_rules=("looks_about_right",))

    def test_duplicate_question_keys_are_refused(self) -> None:
        with pytest.raises(ValueError, match="duplicate question keys"):
            _playbook(questions=(_question(), _question()))

    def test_a_version_below_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="starts at 1"):
            _playbook(version=0)

    def test_every_evaluable_rule_is_one_the_loop_implements(self) -> None:
        """Declaring a rule here is a promise the loop keeps."""
        import inspect

        from app.services.director import loop

        source = inspect.getsource(loop._rules_satisfied)
        for rule in EVALUABLE_COMPLETION_RULES:
            assert rule in source, rule


class TestMatching:
    def test_any_dimension_matching_is_enough(self) -> None:
        """A company whose sector is known and whose industry is not should still get
        its sector's playbook."""
        applies = AppliesTo(sectors=("Health Care",), industries=("Biotechnology",))
        assert applies.matches(sector="Health Care")
        assert applies.matches(industry="biotechnology")
        assert not applies.matches(sector="Financials")

    def test_a_business_model_signal_matches(self) -> None:
        applies = AppliesTo(business_model_signals=("pre_revenue",))
        assert applies.matches(signals={"pre_revenue"})
        assert not applies.matches(signals={"brand_led"})

    def test_matching_folds_case_and_whitespace(self) -> None:
        assert AppliesTo(sectors=("Health Care",)).matches(sector="  health care  ")


class TestSelection:
    def setup_method(self) -> None:
        registry._REGISTRY.clear()

    def teardown_method(self) -> None:
        registry._REGISTRY.clear()

    def test_an_unclassifiable_company_gets_no_playbook_and_a_reason(self) -> None:
        """Applying a bank's methodology to an industrial produces a confident analysis
        of the wrong things, and the wrongness is invisible because every question got
        an answer."""
        registry.register(_playbook())
        selection = registry.select(sector="Energy")
        assert selection.is_empty
        assert "no playbook declares this company" in selection.reason

    def test_every_applicable_playbook_is_returned(self) -> None:
        """A conglomerate is legitimately both industrial and financial."""
        registry.register(
            _playbook(
                playbook_id="industrial",
                applies_to=AppliesTo(sectors=("Industrials",)),
                questions=(_question(key="backlog"),),
            )
        )
        registry.register(
            _playbook(
                playbook_id="banks",
                applies_to=AppliesTo(sectors=("Industrials", "Financials")),
                questions=(_question(key="cet1"),),
            )
        )
        selection = registry.select(sector="Industrials")
        assert len(selection.playbooks) == 2
        assert {q.key for q in selection.questions} == {"backlog", "cet1"}

    def test_completion_rules_are_unioned_so_a_conglomerate_is_harder(self) -> None:
        """ADR-054. Intersecting the rule SETS would make a conglomerate easier to
        declare complete than either of its parts — the outcome the architecture's own
        justification says to avoid."""
        registry.register(
            _playbook(
                playbook_id="a",
                completion_rules=("all_blocking_questions_answered",),
            )
        )
        registry.register(
            _playbook(
                playbook_id="b",
                applies_to=AppliesTo(sectors=("Industrials",)),
                questions=(_question(key="q2"),),
                completion_rules=("no_council_blocking_gaps",),
            )
        )
        selection = registry.select(sector="Industrials")
        assert set(selection.completion_rules) == {
            "all_blocking_questions_answered",
            "no_council_blocking_gaps",
        }

    def test_a_playbook_can_be_named_explicitly(self) -> None:
        registry.register(_playbook(playbook_id="luxury"))
        selection = registry.select(explicit_ids=["luxury"])
        assert selection.versions == {"luxury": 1}
        assert selection.reason == "explicitly named"

    def test_naming_an_unregistered_playbook_yields_nothing(self) -> None:
        assert registry.select(explicit_ids=["nonexistent"]).is_empty

    def test_two_methodologies_cannot_share_a_version(self) -> None:
        """Every run that cited it would be unreproducible."""
        registry.register(_playbook())
        with pytest.raises(registry.DuplicatePlaybookError):
            registry.register(_playbook(questions=(_question(key="different"),)))

    def test_a_new_version_replaces_the_old_one(self) -> None:
        registry.register(_playbook(version=1))
        registry.register(_playbook(version=2))
        assert registry.get("test").version == 2

    def test_the_selection_records_what_a_report_must_state(self) -> None:
        registry.register(_playbook(playbook_id="luxury", version=3))
        payload = registry.select(sector="Industrials").to_dict()
        assert payload["playbook_versions"] == {"luxury": 3}


class TestDirectorIntegration:
    def setup_method(self) -> None:
        registry._REGISTRY.clear()

    def teardown_method(self) -> None:
        registry._REGISTRY.clear()

    async def test_a_playbook_satisfies_the_directors_protocol(self) -> None:
        from app.services.director.planner import PlaybookLike, plan_research

        book = _playbook(
            playbook_id="luxury",
            questions=(
                PlaybookQuestion(
                    key="segment_discipline",
                    text="What are the segment figures, explicitly not Group?",
                    required_tools=frozenset({"get_segment_facts"}),
                    blocking=True,
                ),
            ),
            specialist_roles=("management_analyst",),
        )

        class _Adapter:
            playbook_id = book.playbook_id
            version = book.version

            def mandatory_questions(self):  # noqa: ANN201
                return book.mandatory_questions()

            def specialist_roles(self):  # noqa: ANN201
                return book.specialist_role_ids()

            def completion_rules(self):  # noqa: ANN201
                return book.completion_rule_ids()

        adapter = _Adapter()
        assert isinstance(adapter, PlaybookLike)
        plan = await plan_research(subject="CFR", playbooks=[adapter])
        assert plan.playbook_versions == {"luxury": 1}
        assert plan.questions[0].key == "segment_discipline"
        assert plan.questions[0].blocking is True

    def test_only_a_playbook_can_originate_a_blocking_question(self) -> None:
        """5.2 refuses to let a model set one; this is the other half of the rule."""
        from app.services.ledger import store as ledger

        book = _playbook(questions=(_question(blocking=True),))
        planned = book.mandatory_questions()
        assert planned[0].blocking is True
        assert planned[0].origin == ledger.ORIGIN_PLAYBOOK
