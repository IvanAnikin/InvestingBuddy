"""The four defects that stopped every playbook-gated Council. V3.11 slice 11.3.

V3.10 reported that no playbook-gated Council had ever convened on real data, and read
the three refusals as "the evidence genuinely is not there". Running them down found the
opposite in three cases out of four: the evidence was there and the platform could not
reach it.

Each class below pins one of those, using the real shape that exposed it.
"""

from __future__ import annotations

import pytest

from app.services.agents.investigator import _harvest
from app.services.sources.primary_document_extractor import extract_primary_document
from app.services.sources.verified_issuer_sources import registrable_host_allowed


class TestTheAllowlistWwwAsymmetry:
    """``www.`` was stripped from the host but not from the allowed domain, so an
    allowlist entry carrying the prefix matched nothing at all."""

    def test_a_www_prefixed_entry_now_matches(self) -> None:
        assert registrable_host_allowed("www.example.com", ("www.example.com",))

    def test_a_bare_entry_still_matches_a_www_host(self) -> None:
        assert registrable_host_allowed("www.example.com", ("example.com",))

    def test_a_www_entry_matches_a_bare_host(self) -> None:
        assert registrable_host_allowed("example.com", ("www.example.com",))

    def test_subdomains_still_match(self) -> None:
        assert registrable_host_allowed("reports.example.com", ("example.com",))

    @pytest.mark.parametrize(
        "host",
        [
            "evil-example.com",
            "example.com.attacker.net",
            "wwwexample.com",
            "notexample.com",
            "",
        ],
    )
    def test_the_attacks_are_still_refused(self, host) -> None:
        """Loosening a matcher is how an allowlist stops being one."""
        assert registrable_host_allowed(host, ("example.com",)) is False

    def test_a_trailing_dot_does_not_evade(self) -> None:
        assert registrable_host_allowed("www.example.com.", ("example.com",))


class TestGroupedToolPayloads:
    """``get_segment_facts`` groups by scope, so the citable ids are one level down.
    Harvesting only the top level discarded every real segment fact while the tool
    itself reported success — which is what made the luxury playbook's blocking
    question permanently unanswerable."""

    #: The real payload shape, trimmed.
    GROUPED = {
        "items": [
            {
                "scope_key": "segment:jewellery maisons",
                "reported_names": ["Jewellery Maisons"],
                "facts": [
                    {
                        "fact_id": "11111111-1111-1111-1111-111111111111",
                        "label": "operating_margin",
                        "value_numeric": 30.5,
                        "period_key": "2026",
                        "scope_key": "segment:jewellery maisons",
                    }
                ],
            },
            {
                "scope_key": "segment:specialist watchmakers",
                "reported_names": ["Specialist Watchmakers"],
                "facts": [
                    {
                        "fact_id": "22222222-2222-2222-2222-222222222222",
                        "label": "revenue",
                        "value_numeric": 3.1,
                        "period_key": "2026",
                        "scope_key": "segment:specialist watchmakers",
                    }
                ],
            },
        ]
    }

    def test_nested_records_are_harvested(self) -> None:
        found = _harvest("get_segment_facts", self.GROUPED, False)
        assert [e.citation_id for e in found] == [
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        ]

    def test_each_record_keeps_its_own_scope(self) -> None:
        """A segment group's facts are not interchangeable. Inheriting the group's
        identity instead of the record's is the mixing the invariants forbid."""
        found = _harvest("get_segment_facts", self.GROUPED, False)
        assert {e.scope_key for e in found} == {
            "segment:jewellery maisons",
            "segment:specialist watchmakers",
        }

    def test_each_record_keeps_its_own_period(self) -> None:
        assert all(
            e.period_key == "2026" for e in _harvest("get_segment_facts", self.GROUPED, False)
        )

    def test_a_flat_payload_still_works(self) -> None:
        flat = {"items": [{"fact_id": "abc", "period_key": "2025", "scope_key": "group"}]}
        found = _harvest("get_financial_facts", flat, False)
        assert len(found) == 1
        assert found[0].scope_key == "group"

    def test_a_group_with_no_citable_record_yields_nothing(self) -> None:
        """No invented ids. A group whose records carry none is not evidence."""
        assert (
            _harvest("t", {"items": [{"scope_key": "group", "facts": [{"label": "x"}]}]}, False)
            == []
        )

    def test_nothing_is_read_out_of_free_text(self) -> None:
        assert _harvest("t", {"items": [{"text": "see fact_id 999"}]}, False) == []


class TestPlaybookCalculationsReachTheTool:
    """``get_calculated_metrics`` refuses without a metric name, and the planner was
    dropping the playbook's ``required_calculations`` — so the calculation leg of every
    playbook question silently never ran."""

    def test_a_planned_question_carries_its_calculations(self) -> None:
        from app.services.director.planner import PlannedQuestion

        q = PlannedQuestion(
            key="segment_discipline",
            text="x",
            origin="playbook",
            required_calculations=("segment_revenue_mix",),
        )
        assert q.required_calculations == ("segment_revenue_mix",)

    def test_the_luxury_playbook_still_declares_one(self) -> None:
        from app.services.playbooks import industries, registry  # noqa: F401

        luxury = next(p for p in registry.all_playbooks() if p.playbook_id == "luxury")
        question = next(q for q in luxury.questions if q.key == "segment_discipline")
        assert "segment_revenue_mix" in question.required_calculations

    def test_every_declared_calculation_is_implemented(self) -> None:
        """A playbook naming a calculation the engine does not have would block the
        Council forever, which is exactly how this class of defect presents."""
        from app.services.agents.investigator import CALCULATION_NAMES
        from app.services.playbooks import industries, registry  # noqa: F401

        for playbook in registry.all_playbooks():
            for question in playbook.questions:
                unknown = set(question.required_calculations) - CALCULATION_NAMES
                assert not unknown, f"{playbook.playbook_id}/{question.key}: {unknown}"

    def test_the_arguments_name_the_metric(self) -> None:
        import uuid

        from app.services.agent_tools.contracts import TOOL_GET_CALCULATED_METRICS
        from app.services.agents.investigator import _tool_arguments
        from app.services.director.planner import PlannedQuestion

        company = uuid.uuid4()
        args = _tool_arguments(
            TOOL_GET_CALCULATED_METRICS,
            PlannedQuestion(
                key="k",
                text="t",
                origin="playbook",
                required_calculations=("segment_revenue_mix",),
            ),
            company,
        )
        assert args == {"company_id": str(company), "metrics": ["segment_revenue_mix"]}

    def test_a_question_naming_no_calculation_makes_no_call(self) -> None:
        import uuid

        from app.services.agent_tools.contracts import TOOL_GET_CALCULATED_METRICS
        from app.services.agents.investigator import _tool_arguments
        from app.services.director.planner import PlannedQuestion

        args = _tool_arguments(
            TOOL_GET_CALCULATED_METRICS,
            PlannedQuestion(key="k", text="t", origin="director"),
            uuid.uuid4(),
        )
        assert args is None

    def test_an_unimplemented_calculation_is_dropped_not_sent(self) -> None:
        import uuid

        from app.services.agent_tools.contracts import TOOL_GET_CALCULATED_METRICS
        from app.services.agents.investigator import _tool_arguments
        from app.services.director.planner import PlannedQuestion

        args = _tool_arguments(
            TOOL_GET_CALCULATED_METRICS,
            PlannedQuestion(
                key="k", text="t", origin="playbook", required_calculations=("moon_phase",)
            ),
            uuid.uuid4(),
        )
        assert args is None


class TestModernFilingHtml:
    """A current SEC Inline-XBRL filing is built from styled ``<div>``s, not ``<p>``.
    Parsing only the classic block tags returned ZERO text from a 2.7 MB 10-K while
    still finding its tables."""

    #: The real shape: divs and spans, no <p> anywhere.
    FILING = b"""<html><body>
      <div><span style="font-weight:700">Item 1. Business</span></div>
      <div><span>We are a biotechnology company advancing messenger RNA science. Our
      clinical pipeline includes mRNA-1345, an RSV vaccine approved by the FDA for
      adults aged 60 years and older, together with several Phase 3 programs.</span></div>
      <div><span>Our respiratory portfolio also includes a combination candidate that
      entered a pivotal study during the year and remains under active development.</span></div>
      <table><tr><td>Revenue</td><td>1,234</td></tr></table>
    </body></html>"""

    def test_the_corpus_path_reads_the_prose(self) -> None:
        result = extract_primary_document(self.FILING, document_type="html", capture_blocks=True)
        assert result.blocks
        text = " ".join(b.text for b in result.blocks)
        assert "mRNA-1345" in text
        assert "Phase 3" in text

    def test_the_v2_path_is_unchanged(self) -> None:
        """Opt-in on purpose: V2 is the deployed, approved product and this changes
        which excerpts it ranks. V2 remains byte-identical until somebody decides
        otherwise."""
        assert extract_primary_document(self.FILING, document_type="html").blocks == []

    def test_tables_are_found_either_way(self) -> None:
        for capture in (False, True):
            result = extract_primary_document(
                self.FILING, document_type="html", capture_blocks=capture
            )
            assert result.tables

    def test_a_container_div_does_not_duplicate_its_children(self) -> None:
        """Nesting is the risk: a naive fix emits the same sentence once per level."""
        nested = (
            b"<html><body><div><div><div>"
            + b"The quick brown fox jumps over the lazy dog repeatedly and at length. " * 2
            + b"</div></div></div></body></html>"
        )
        result = extract_primary_document(nested, document_type="html", capture_blocks=True)
        assert len(result.blocks) == 1

    def test_classic_paragraph_markup_still_works(self) -> None:
        classic = (
            b"<html><body><h2>Business</h2><p>"
            + b"We manufacture lithography systems for the semiconductor industry worldwide."
            + b"</p></body></html>"
        )
        result = extract_primary_document(classic, document_type="html", capture_blocks=True)
        assert any("lithography" in b.text for b in result.blocks)

    def test_headings_still_become_sections(self) -> None:
        marked = (
            b"<html><body><h1>Segment information</h1><div>"
            + b"Revenue by business area is presented below for each reportable segment."
            + b"</div></body></html>"
        )
        result = extract_primary_document(marked, document_type="html", capture_blocks=True)
        assert any(b.section == "Segment information" for b in result.blocks)
