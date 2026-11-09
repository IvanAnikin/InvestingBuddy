"""ADR-093 — segment hierarchy and period duration (QA closure batch H3b).

JPM3 (report 732c044d): the executive synthesis called a business line ("Markets") the
company's main revenue SEGMENT; two findings stated one segment's nonperforming assets
as the "total" and the chair restated them as the firm's; a finding labelled NINE-MONTH
business-line revenue "Q3". Synthetic fixtures modelled on that filing (issuer names
appear in tests only). No network.
"""

from __future__ import annotations

import time
import uuid
from types import SimpleNamespace

import pytest

from app.services.agents import investigator as inv
from app.services.council_v2.inputs import FindingRef
from app.services.ledger import claim_checks as cc
from app.services.ledger import claim_period as cp
from app.services.ledger import period_duration as pd
from app.services.ledger import report_invariants as ri
from app.services.ledger import segment_hierarchy as sh
from app.services.pipeline import segment_scope

# ── Fixtures ──────────────────────────────────────────────────────────────── #

LIST_SENTENCE = (
    "For management reporting purposes, the Firm has three reportable business segments – "
    "Consumer & Community Banking (“CCB”), Commercial & Investment Bank (“CIB”) and Asset "
    "& Wealth Management (“AWM”) – with the remaining activities in Corporate."
)
FILING = "\n".join([
    "Overview",
    LIST_SENTENCE,
    "BUSINESS SEGMENT & CORPORATE RESULTS",
    "The Firm is managed on a line of business basis.",
    "CONSUMER & COMMUNITY BANKING",
    "Card Services & Auto revenue was $7.1 billion, up 9%.",
    "Home Lending revenue was $1.3 billion, up 4%.",
    "COMMERCIAL & INVESTMENT BANK",
    "Quarterly results",
    "Net revenue was $22.7 billion, up 14%.",
    "• Payments revenue was $5.6 billion, up 14%.",
    "• Lending revenue was $2.2 billion, up 20%.",
    "Markets & Securities Services revenue was $11.8 billion, up 13%. Markets revenue was "
    "$10.3 billion, up 15%.",
    "• Fixed Income Markets revenue was $5.4 billion, down 4%.",
    "• Securities Services revenue was $1.6 billion, up 14%.",
    "Total nonperforming assets $ 5,496 $ 5,792 (5)",
    "ASSET & WEALTH MANAGEMENT",
    "Asset Management revenue was $2.9 billion.",
    "CORPORATE",
    "Net revenue was $93 million.",
    "FIRMWIDE RISK MANAGEMENT",
    "The Firm's nonperforming assets totaled $10.0 billion at September 30, 2026.",
])


def _hierarchy() -> sh.SegmentHierarchy:
    built = sh.build_hierarchy([FILING])
    assert built is not None
    return built


# ── Reportable segments ───────────────────────────────────────────────────── #


class TestReportableSegments:
    def test_list_with_acronyms_and_count(self) -> None:
        names = sh.reportable_segments_from_text(LIST_SENTENCE)
        assert [(s.name, s.acronym) for s in names] == [
            ("Consumer & Community Banking", "CCB"),
            ("Commercial & Investment Bank", "CIB"),
            ("Asset & Wealth Management", "AWM"),
        ]

    def test_oxford_comma_and_colon(self) -> None:
        text = ("We manage our business through four reportable business segments: "
                "Aerospace Technologies, Industrial Automation, Building Automation, and "
                "Energy and Sustainability Solutions.")
        names = [s.name for s in sh.reportable_segments_from_text(text)]
        assert names == ["Aerospace Technologies", "Industrial Automation",
                         "Building Automation", "Energy and Sustainability Solutions"]

    def test_count_settles_an_and_inside_a_name(self) -> None:
        text = ("our three reportable segments are Building Automation, Industrial "
                "Automation and Energy and Sustainability Solutions.")
        # Two "and"s in the tail: one name or two cannot be told apart.
        assert sh.reportable_segments_from_text(text) == ()

    def test_count_mismatch_is_no_list(self) -> None:
        text = "The Company has three reportable segments: Alpha Mining, Beta Metals."
        assert sh.reportable_segments_from_text(text) == ()

    def test_no_count_and_ambiguous_and_is_no_list(self) -> None:
        text = "Our reportable segments are Alpha Mining, Beta and Gamma Solutions."
        assert sh.reportable_segments_from_text(text) == ()

    def test_past_structure_loses_to_the_current_list(self) -> None:
        text = (
            "Prior to the Spin-Off, we managed our business through four reportable "
            "business segments: Aerospace Technologies, Building Automation, Process "
            "Automation and Technology, and Industrial Automation. We now manage our "
            "businesses through three reportable business segments: Building Automation, "
            "Process Automation and Technology, and Industrial Automation."
        )
        names = [s.name for s in sh.reportable_segments_from_text(text)]
        assert names == ["Building Automation", "Process Automation and Technology",
                         "Industrial Automation"]

    def test_a_tie_between_two_lists_is_no_list(self) -> None:
        text = ("We have two reportable segments: Alpha Mining and Beta Metals. "
                "We have two reportable segments: Gamma Mining and Delta Metals.")
        assert sh.reportable_segments_from_text(text) == ()

    def test_lowercase_items_are_no_list(self) -> None:
        assert sh.reportable_segments_from_text(
            "two reportable segments: mining and processing.") == ()

    def test_xbrl_members(self) -> None:
        names = [s.name for s in sh.segments_from_xbrl_members(
            ["jpm:CommercialAndInvestmentBankMember", "Asset and Wealth Management",
             "Non US (geographic)"])]
        assert names == ["Commercial And Investment Bank", "Asset and Wealth Management"]
        assert sh.name_key("Commercial And Investment Bank") == sh.name_key(
            "Commercial & Investment Bank")

    def test_xbrl_members_stand_in_when_no_list(self) -> None:
        built = sh.build_hierarchy(["no list here"], xbrl_members=[
            "x:AlphaMiningMember", "x:BetaMetalsMember"])
        assert built is not None
        assert built.basis == (sh.BASIS_XBRL,)
        assert sh.build_hierarchy(["no list"], xbrl_members=["x:AlphaMember"]) is None

    def test_xbrl_confirms_the_list(self) -> None:
        built = sh.build_hierarchy([LIST_SENTENCE], xbrl_members=[
            "jpm:CommercialAndInvestmentBankMember"])
        assert built is not None and sh.BASIS_XBRL in built.basis


# ── Sections and business lines ───────────────────────────────────────────── #


class TestBusinessLines:
    def test_lines_belong_to_their_section(self) -> None:
        h = _hierarchy()
        lines = {b.name: b.segment for b in h.business_lines}
        assert lines["Payments"] == "Commercial & Investment Bank"
        assert lines["Markets"] == "Commercial & Investment Bank"
        assert lines["Fixed Income Markets"] == "Commercial & Investment Bank"
        assert lines["Home Lending"] == "Consumer & Community Banking"
        assert lines["Asset Management"] == "Asset & Wealth Management"
        # Totals, generic lines and the non-segment Corporate section are no lines.
        assert "Net" not in lines and "Total" not in lines
        assert sh.BASIS_SECTIONS in h.basis

    def test_a_name_under_two_segments_is_dropped(self) -> None:
        text = FILING.replace("Asset Management revenue", "Payments revenue")
        h = sh.build_hierarchy([text])
        assert h is not None and h.line("Payments") is None

    def test_sections_end_at_the_next_heading(self) -> None:
        h = _hierarchy()
        sections = sh.segment_sections(FILING, h.segments)
        assert [s for _a, _b, s in sections] == [
            "Consumer & Community Banking", "Commercial & Investment Bank",
            "Asset & Wealth Management"]
        cib = next((a, b) for a, b, s in sections if s.startswith("Commercial"))
        assert "Corporate" not in FILING[cib[0]:cib[1]].title().split("\n")[-1]
        assert "$93 million" not in FILING[cib[0]:cib[1]]

    def test_chunk_sections(self) -> None:
        h = _hierarchy()
        start = FILING.index("COMMERCIAL & INVESTMENT BANK")
        npa = FILING.index("Total nonperforming assets")
        firm = FILING.index("FIRMWIDE RISK MANAGEMENT")
        chunks = [
            ("c:head", start, FILING[start:start + 40]),
            ("c:npa", npa, FILING[npa:npa + 40]),
            ("c:firm", firm + 30, FILING[firm + 30:firm + 80]),
            ("c:split", npa, FILING[npa:FILING.index("Asset Management revenue")]),
        ]
        sections = sh.chunk_sections_of(chunks, h.segments)
        assert sections["c:head"] == "Commercial & Investment Bank"
        # Unread text between the heading and the chunk: a heading may hide in it.
        assert "c:npa" not in sections and "c:firm" not in sections
        assert "c:split" not in sections  # crosses into another section

    def test_chunk_sections_over_contiguous_chunks(self) -> None:
        h = _hierarchy()
        chunks = []
        for i, line_start in enumerate(range(0, len(FILING), 50)):
            chunks.append((f"c:{i}", line_start, FILING[line_start:line_start + 50]))
        sections = sh.chunk_sections_of(chunks, h.segments)
        npa = FILING.index("Total nonperforming assets $ 5,496")
        firm = FILING.index("The Firm's nonperforming assets")

        def chunk_at(pos: int) -> str:
            return next(cid for cid, start, text in chunks if start <= pos < start + 50)

        assert sections.get(chunk_at(npa + 5)) == "Commercial & Investment Bank"
        assert chunk_at(firm + 5) not in sections
        assert chunk_at(FILING.index("$93 million")) not in sections

    def test_round_trip_and_malformed(self) -> None:
        h = _hierarchy()
        again = sh.SegmentHierarchy.from_dict(h.to_dict())
        assert again is not None
        assert again.segments == h.segments and again.business_lines == h.business_lines
        assert sh.SegmentHierarchy.from_dict(None) is None
        assert sh.SegmentHierarchy.from_dict({"reportable_segments": "x"}) is None
        bad = sh.SegmentHierarchy.from_dict({
            "reportable_segments": [{"name": "Alpha"}, {"name": "Beta"}],
            "business_lines": [{"name": "Gamma", "segment": "Unknown"}, "x"]})
        assert bad is not None and bad.business_lines == ()


# ── Reading statements and scoping findings ───────────────────────────────── #


class TestScopeFinding:
    def test_longest_name_and_case(self) -> None:
        h = _hierarchy()
        named = sh.names_in("Fixed Income Markets $5.4bn; dividend payments $1bn", h)
        assert [b.name for b in named.lines] == ["Fixed Income Markets"]

    def test_mentioned_line_is_not_an_owner(self) -> None:
        h = _hierarchy()
        assert sh.names_in("NII excluding Markets $92,419M", h).lines == ()
        assert sh.names_in("NII ~$103bn (ex-Markets ~$95bn)", h).lines == ()
        assert sh.names_in("excluding Markets", h, owned_only=False).lines

    def test_f4_business_lines(self) -> None:
        h = _hierarchy()
        scope = sh.scope_finding(
            "Q3 FY2026 segment revenue: Payments $16.0bn, Lending $6.4bn, "
            "Securities Services $4.8bn, Markets $34.0bn.", h)
        assert scope is not None
        assert scope.scope_key == "segment:commercial & investment bank"
        assert scope.scope_type == sh.SCOPE_TYPE_BUSINESS_LINE
        assert scope.label == "[business lines of the Commercial & Investment Bank segment]"
        assert sh.labelled("x", scope).startswith(scope.label)
        assert sh.labelled(sh.labelled("x", scope), scope).count(scope.label) == 1

    def test_named_segment_needs_no_label(self) -> None:
        scope = sh.scope_finding("CIB revenue $22.7bn, up 14%.", _hierarchy())
        assert scope is not None and scope.label is None
        assert scope.scope_type == sh.SCOPE_TYPE_SEGMENT

    def test_a_figure_before_any_name_is_the_company_s(self) -> None:
        assert sh.scope_finding(
            "Total backlog $38.3bn; Payments $9.3bn", _hierarchy()) is None

    def test_firm_wording_and_two_segments(self) -> None:
        h = _hierarchy()
        assert sh.scope_finding("Firm revenue: Payments $16.0bn", h) is None
        assert sh.scope_finding("Payments $16.0bn and Home Lending $1.3bn", h) is None
        assert sh.scope_finding("Payments $16.0bn", None) is None

    def test_source_section(self) -> None:
        h = _hierarchy()
        statement = "Q3 2026: total nonperforming assets $5,496m vs $5,792m (-5%)."
        cib = "Commercial & Investment Bank"
        scope = sh.scope_finding(statement, h, cited=[(cib, "x")], figure_found=True)
        assert scope is not None and scope.basis == sh.BASIS_SOURCE_SECTION
        assert scope.label == f"[{cib} segment, not the whole company]"
        # No figure found there, two sections, an unsectioned passage: nothing.
        assert sh.scope_finding(statement, h, cited=[(cib, "x")]) is None
        assert sh.scope_finding(statement, h, cited=[(cib, "x"), (None, "y")],
                                figure_found=True) is None
        assert sh.scope_finding("Firm NPAs $5,496m", h, cited=[(cib, "x")],
                                figure_found=True) is None


# ── Duration ──────────────────────────────────────────────────────────────── #

QUARTER_AND_YTD = "\n".join([
    "Quarterly results",
    "Payments revenue was $5.6 billion, up 14%.",
    "Markets revenue was $10.3 billion, up 15%.",
    "Year-to-date results",
    "Payments revenue was $16.0 billion, up 13%.",
    "Lending revenue was $6.4 billion, up 13%.",
    "Markets revenue was $34.0 billion, up 23%.",
])
TABLE = "\n".join([
    "Selected income statement data",
    "Three months ended September 30, | Nine months ended September 30,",
    "(in millions) | 2026 | 2025 | Change | 2026 | 2025 | Change",
    "Payments | 5,620 | 4,917 | 14 | 16,039 | 14,217 | 13",
    "Lending | 2,240 | 1,872 | 20 | 6,370 | 5,616 | 13",
])


class TestDuration:
    def test_statement_states_nine_months(self) -> None:
        rk = pd.rekey_for_duration(
            "2026-Q3", "Nine-month 2026 reported NII was $77,913M, versus $70,448M in "
            "nine-month 2025.", [])
        assert rk is not None and rk.period_key == "2026-9M"
        assert rk.basis == pd.BASIS_STATEMENT

    def test_three_months_stays_a_quarter(self) -> None:
        assert pd.rekey_for_duration(
            "2026-Q3", "NII was $27,036M for the 3 months ended September 30, 2026.",
            [QUARTER_AND_YTD]) is None
        assert pd.rekey_for_duration(
            "2026-Q3", "Q3 2026 NII $27,036M; nine-month $77,913M.", []) is None

    def test_nine_month_prose_labelled_q3_is_rekeyed_and_relabelled(self) -> None:
        rk = pd.rekey_for_duration(
            "FY2026-Q3", "Q3 FY2026 segment revenue: Payments $16.0bn, Lending $6.4bn, "
            "Markets $34.0bn.", [QUARTER_AND_YTD])
        assert rk is not None
        assert rk.period_key == "FY2026-9M" and rk.basis == pd.BASIS_SOURCE
        assert rk.statement.startswith("9M FY2026 (year to date) segment revenue")

    def test_quarter_figures_stay(self) -> None:
        assert pd.rekey_for_duration(
            "2026-Q3", "Q3 2026: Payments $5.6bn, Markets $10.3bn.",
            [QUARTER_AND_YTD]) is None

    def test_a_figure_in_both_contexts_stays(self) -> None:
        both = QUARTER_AND_YTD + "\nQuarterly results\nLending revenue was $6.4 billion."
        assert pd.rekey_for_duration("2026-Q3", "Q3 2026: Lending $6.4bn", [both]) is None

    def test_sec_two_column_table(self) -> None:
        rk = pd.rekey_for_duration("2026-Q3", "Q3 2026 Payments revenue $16.0bn", [TABLE])
        assert rk is not None and rk.period_key == "2026-9M"
        assert pd.rekey_for_duration(
            "2026-Q3", "Q3 2026 Payments revenue $5.6bn", [TABLE]) is None

    def test_quarter_end_balance_sheet_is_never_rekeyed(self) -> None:
        balance = "\n".join([
            "Consolidated balance sheets",
            "September 30, 2026 | December 31, 2025",
            "Deposits | 2,780,793 | 2,559,320",
        ])
        assert pd.rekey_for_duration(
            "2026-Q3", "Deposits $2,780,793m at 30 Sep 2026.", [balance]) is None

    def test_six_months_is_the_half(self) -> None:
        text = "Six months ended June 30, 2026\nRevenue was $9.1 billion."
        rk = pd.rekey_for_duration("2026-Q2", "Q2 2026 revenue $9.1bn", [text])
        assert rk is not None and rk.period_key == "2026-H1"
        assert rk.statement.startswith("H1 2026 revenue")

    def test_only_quarter_keys_and_known_lengths(self) -> None:
        assert pd.rekey_for_duration("FY2025", "nine months $1m", []) is None
        assert pd.rekey_for_duration("2026-Q1", "year to date $1m", []) is None
        assert pd.rekey_for_duration("2026-Q4", "nine months $1m", []) is None
        assert pd.rekey_for_duration(None, "nine months $1m", []) is None
        assert pd.ytd_key(2026, 12) is None

    def test_claim_period_reads_ytd(self) -> None:
        p = cp.period_of_key("2026-9M")
        assert p is not None and p.span == cp.SPAN_NINE
        assert p.human() == "9M 2026 (year to date)"
        nine = cp.mentions("for the nine months ended September 30, 2026")[0]
        q3 = cp.period_of_key("2026-Q3")
        assert q3 is not None
        assert cp.compatible(p, nine) and not cp.compatible(p, q3)
        assert [m.key for m in cp.mentions("9M 2026 (year to date) revenue")] == ["2026-9M"]
        assert cp.is_ytd_key("FY2026-9M") and not cp.is_ytd_key("2026-Q3")


# ── Claim checks ──────────────────────────────────────────────────────────── #

P0 = ("JPMorgan Chase's revenue is primarily driven by its Markets segment, followed by "
      "Payments and Lending, with recent quarterly data showing a slight decline in "
      "segment revenues compared to the prior year.")


class TestHierarchyClaims:
    def test_p0_main_driver_is_withheld(self) -> None:
        verdict = cc.check_hierarchy(P0, _hierarchy())
        assert verdict is not None and verdict.replacement is None
        assert verdict.rule == cc.RULE_LINE_AS_MAIN_DRIVER

    def test_largest_revenue_segment_is_withheld(self) -> None:
        verdict = cc.check_hierarchy(
            "Markets is the largest revenue segment, followed by Payments.", _hierarchy())
        assert verdict is not None and verdict.rule == cc.RULE_LINE_AS_MAIN_DRIVER

    def test_line_called_a_segment_is_rewritten(self) -> None:
        verdict = cc.check_hierarchy("The Payments segment grew revenue 14%.", _hierarchy())
        assert verdict is not None and verdict.rule == cc.RULE_LINE_AS_SEGMENT
        assert verdict.replacement == ("The Payments business (part of the Commercial & "
                                       "Investment Bank segment) grew revenue 14%.")

    def test_kept(self) -> None:
        h = _hierarchy()
        for claim in (
            "Revenue is primarily driven by its Commercial & Investment Bank segment.",
            "Payments revenue grew 14% in the quarter.",
            "Markets segments are volatile.",
        ):
            assert cc.check_hierarchy(claim, h) is None, claim
        assert cc.check_hierarchy(P0, None) is None
        single = sh.SegmentHierarchy((sh.Segment("Alpha"),),
                                     (sh.BusinessLine("Markets", "Alpha"),))
        assert cc.check_hierarchy(P0, single).rule == cc.RULE_LINE_AS_SEGMENT

    def test_names_scope_with_hierarchy(self) -> None:
        h = _hierarchy()
        name = "commercial & investment bank"
        assert not cc._names_scope("JPMorgan Chase Bank NPAs fell", name, h)
        assert cc._names_scope("CIB NPAs fell", name, h)
        assert cc._names_scope("Markets revenue rose", name, h)

    def test_figureless_metric_scope(self) -> None:
        h = _hierarchy()
        npa = cc.ClaimSource("a", "Q3 2026: total nonperforming assets $5,496m vs $5,792m",
                             "2026-Q3", "segment:commercial & investment bank")
        npa2 = cc.ClaimSource("b", "FY2025 total nonperforming assets $5,555m",
                              "FY2025", "segment:commercial & investment bank")
        provision = cc.ClaimSource("c", "Q3 2026 provision for credit losses $2,743M",
                                   "2026-Q3", None)
        claim = ("Credit quality indicators show a modest reduction in nonperforming assets "
                 "and loans, alongside a decrease in provision for credit losses.")
        verdict = cc.check_claim(claim, [provision, npa, npa2], h)
        assert verdict is not None and verdict.rule == cc.RULE_CLAIM_SCOPE
        assert set(verdict.source_ids) == {"a", "b"}
        firm = cc.ClaimSource("d", "Firm nonperforming assets $9,996m", "2026-Q3", "group")
        assert cc.check_claim(claim, [provision, npa, firm], h) is None
        assert cc.check_claim("CIB nonperforming assets fell modestly.", [npa], h) is None

    def test_nine_month_figure_said_of_q3_is_withheld(self) -> None:
        h = _hierarchy()
        f4 = cc.ClaimSource(
            "f4", "[business lines of the Commercial & Investment Bank segment] 9M 2026 "
            "(year to date) segment revenue: Payments $16.0bn, Markets $34.0bn.",
            "2026-9M", "segment:commercial & investment bank")
        verdict = cc.check_claim("Markets revenue was $34.0bn in Q3 2026.", [f4], h)
        assert verdict is not None and verdict.rule == cc.RULE_CLAIM_PERIOD
        assert cc.check_claim("Markets revenue was $34.0bn in 9M 2026.", [f4], h) is None

    def test_verbatim_copy_is_not_period_checked(self) -> None:
        source = cc.ClaimSource(
            "s", "For the year to 30 June 2025 total segment revenue was $277,599 "
            "(Australia $277,342; Malawi $257).", "2026", "group")
        claim = ("Business model: For the year to 30 June 2025 total segment revenue was "
                 "$277,599 (Australia $277,342; Malawi $257).")
        assert cc.check_claim(claim, [source]) is None

    def test_source_of_finding_takes_the_hierarchy_scope(self) -> None:
        h = _hierarchy()
        source = cc.source_of_finding(
            {"finding_id": "f", "statement": "FY2025: Payments $19.3bn, Lending $7.6bn"}, h)
        assert source is not None
        assert source.scope == "segment:commercial & investment bank"
        plain = cc.source_of_finding(
            {"finding_id": "f", "statement": "FY2025: Payments $19.3bn, Lending $7.6bn"})
        assert plain is not None and plain.scope is None


# ── The enforcer ──────────────────────────────────────────────────────────── #

F1, F4, F18, F19, F11 = (str(uuid.UUID(int=i)) for i in range(1, 6))


def _payload(*, complete: bool = True) -> tuple[dict, dict]:
    findings = [
        {"finding_id": F1, "statement": "FY2025: Markets & Securities Services revenue "
         "$41.3bn (Markets $35.8bn), Payments $19.3bn, Lending $7.6bn", "period_key": "2025"},
        {"finding_id": F4, "statement": "Q3 FY2026 segment revenue: Payments $16.0bn, "
         "Lending $6.4bn, Markets $34.0bn.", "period_key": "2026-Q3"},
        {"finding_id": F18, "statement": "[Commercial & Investment Bank segment, not the "
         "whole company] Q3 2026: total nonperforming assets $5,496m vs $5,792m.",
         "period_key": "2026-Q3", "scope_key": "segment:commercial & investment bank"},
        {"finding_id": F19, "statement": "[Commercial & Investment Bank segment, not the "
         "whole company] FY2025 total nonperforming assets $5,555m vs $5,118m.",
         "period_key": "2025", "scope_key": "segment:commercial & investment bank"},
        {"finding_id": F11, "statement": "Q3 2026 provision for credit losses $2,743M.",
         "period_key": "2026-Q3"},
    ]
    labels = {F1: "F1", F4: "F4", F18: "F18", F19: "F19", F11: "F11"}
    v3 = {
        "findings": findings,
        "segment_hierarchy": _hierarchy().to_dict(),
        "chair": {"synthesis": "Adverse signals include rising nonperforming assets in "
                               "FY2025 compared to prior years. Net income rose."},
        "professional_research": {
            "finding_labels": labels,
            "sections": [
                {"key": "executive_synthesis", "sentences": [
                    {"text": P0, "labels": ["F1", "F4"]},
                    {"text": "Credit quality indicators show a modest reduction in "
                             "nonperforming assets.", "labels": ["F11", "F18", "F19"]},
                    {"text": "Provision for credit losses fell.", "labels": ["F11"]},
                ]},
                {"key": "business_model", "lead": {
                    "text": "Markets is the largest revenue segment.", "labels": ["F1"]}},
            ],
        },
    }
    state = {"findings": {"total": len(findings) if complete else len(findings) + 5}}
    return v3, state


class TestEnforcer:
    def test_synthesis_lead_and_chair(self) -> None:
        v3, state = _payload()
        result = ri.enforce(v3, state)
        rules = {(v.path, v.rule) for v in result.violations}
        assert ("professional_research.sections[0].sentences[0]",
                ri.RULE_LINE_AS_MAIN_DRIVER) in rules
        assert ("professional_research.sections[0].sentences[1]",
                ri.RULE_CLAIM_SCOPE) in rules
        assert ("professional_research.sections[1].lead", ri.RULE_LINE_AS_MAIN_DRIVER) in rules
        assert ("chair.synthesis", ri.RULE_CLAIM_SCOPE) in rules
        sentences = result.v3["professional_research"]["sections"][0]["sentences"]
        assert sentences[2]["text"] == "Provision for credit losses fell."
        assert result.v3["chair"]["synthesis"] == "Net income rose."

    def test_uncited_chair_needs_the_whole_ledger(self) -> None:
        v3, state = _payload(complete=False)
        result = ri.enforce(v3, state)
        assert not any(v.path == "chair.synthesis" for v in result.violations)

    def test_no_hierarchy_no_hierarchy_rules(self) -> None:
        v3, state = _payload()
        v3.pop("segment_hierarchy")
        result = ri.enforce(v3, state)
        assert not any(v.rule in (ri.RULE_LINE_AS_MAIN_DRIVER, ri.RULE_LINE_AS_SEGMENT)
                       for v in result.violations)
        assert not any(v.path == "chair.synthesis" for v in result.violations)

    def test_legacy_line_as_segment(self) -> None:
        v3, state = _payload()
        legacy = {"agents": [{"key_points": [
            {"claim": "The Payments segment grew 14%.", "citation_ids": ["E1"]}]}]}
        result = ri.enforce(v3, state, llm_council=legacy)
        assert result.llm_council is not None
        claim = result.llm_council["agents"][0]["key_points"][0]["claim"]
        assert "business (part of the Commercial & Investment Bank segment)" in claim


# ── The investigator ──────────────────────────────────────────────────────── #


def _evidence(cid: str, text: str) -> inv._Evidence:
    return inv._Evidence(citation_id=cid, kind="search_corpus", text=text)


class TestInvestigator:
    def test_ytd_rekey_helper(self) -> None:
        ev = [_evidence("ev:c:1", QUARTER_AND_YTD)]
        rk = inv._ytd_rekey("2026-Q3", "Q3 2026: Payments $16.0bn, Markets $34.0bn",
                            ["ev:c:1"], ev)
        assert rk is not None and rk.period_key == "2026-9M"
        assert inv._ytd_rekey(None, "x", ["ev:c:1"], ev) is None

    def test_segment_scope_from_the_cited_chunk_section(self) -> None:
        h = _hierarchy()
        h = sh.SegmentHierarchy(h.segments, h.business_lines, h.basis,
                                {"c:npa": "Commercial & Investment Bank"})
        ev = [_evidence("ev:c:npa", "Total nonperforming assets $ 5,496 $ 5,792 (5)")]
        scope = inv._segment_scope("Q3 2026: total nonperforming assets $5,496m",
                                   ["ev:c:npa"], ev, h)
        assert scope is not None and scope.scope_key == "segment:commercial & investment bank"
        # The figure is not in the cited text: nothing.
        assert inv._segment_scope("Q3 2026: total nonperforming assets $9,996m",
                                  ["ev:c:npa"], ev, h) is None

    def test_diagnostics(self) -> None:
        a, b = inv.InvestigatorDiagnostics(), inv.InvestigatorDiagnostics()
        b.statements_ytd_rekeyed, b.statements_scoped_to_segment = 2, 3
        a.merge(b)
        assert a.to_dict()["statements_ytd_rekeyed"] == 2
        assert a.to_dict()["statements_scoped_to_segment"] == 3
        assert inv.LLMInvestigator(session=None, company_id=uuid.uuid4()).segment_hierarchy is None

    def test_finding_ref_scope_type_and_basis(self) -> None:
        row = SimpleNamespace(
            id=uuid.uuid4(), statement="s", mechanism=None, direction=None, confidence=None,
            evidence_ids_json=["ev:c:1"], calculation_ids_json=None, period_key="2026-9M",
            scope_key="segment:x", originating_role="r", verification_status="unverified",
            period_type="source_duration", scope_type="business_line")
        out = FindingRef.from_row(row).to_dict()
        assert out["period_basis"] == "source_duration"
        assert out["scope_type"] == "business_line"
        row.period_type, row.scope_type = "annual", "brand"
        out = FindingRef.from_row(row).to_dict()
        assert "period_basis" not in out and "scope_type" not in out


class TestPipelineLoader:
    def test_periodic_versions(self) -> None:
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        rows = [
            {"version_id": a, "document_type": "interim_report", "chunks": 5},
            {"version_id": str(uuid.uuid4()), "document_type": "results_release", "chunks": 9},
            {"version_id": b, "document_type": "annual_report", "chunks": 0},
            {"version_id": "bad", "document_type": "10-K", "chunks": 3},
        ]
        assert segment_scope.periodic_versions(rows) == [uuid.UUID(a)]
        assert segment_scope.periodic_versions(None) == []

    @pytest.mark.asyncio
    async def test_unknown_or_failing_session(self) -> None:
        assert await segment_scope.load_segment_hierarchy(None, uuid.uuid4(), []) is None

        class Broken:
            def begin_nested(self):  # noqa: ANN201
                raise RuntimeError("down")

        rows = [{"version_id": str(uuid.uuid4()), "document_type": "10-Q", "chunks": 3}]
        assert await segment_scope.load_segment_hierarchy(Broken(), uuid.uuid4(), rows) is None

    def test_outcome_serialises_the_hierarchy_only_when_read(self) -> None:
        from app.services.pipeline.v3_pipeline import V3ResearchOutcome

        outcome = V3ResearchOutcome()
        assert "segment_hierarchy" not in outcome.to_dict()
        outcome.segment_hierarchy = _hierarchy().to_dict()
        assert outcome.to_dict()["segment_hierarchy"]["reportable_segments"]


# ── Linear time ───────────────────────────────────────────────────────────── #

PATTERNS = [
    sh._LIST_CUE_RE, sh._LIST_LEAD_RE, sh._LIST_END_RE, sh._ACRONYM_RE, sh._NAME_WORD_RE,
    sh._HEADING_CHARS_RE, sh._LINE_REVENUE_RE, sh._FIRM_WORDS_RE, sh._MENTION_BEFORE_RE,
    sh._PAST_STRUCTURE_RE, sh._FIGURE_RE, pd._YTD_WORDS_RE, pd._QUARTER_WORDS_RE,
    pd._MARKER_LINE_RE, pd._HEADER_DURATION_RE, pd._CELL_RE, pd._SENTENCE_END_RE,
    cp._KEY_YTD_RE, cp._YTD_LABEL_RE, cc._DRIVER_BEFORE_RE, cc._DRIVER_AFTER_RE,
    cc._AS_SEGMENT_AFTER_RE, cc._REVENUE_WORDS_RE, cc._METRIC_WORD_RE,
]
DEGENERATE = [" " * 10000, "\n" * 10000, "A-" * 3000, "a," * 5000, "a." * 3000,
              "Aa " * 3000, "& " * 4000, "1," * 4000, "9M " * 3000,
              "primarily driven by " * 500, "three months ended " * 500]


@pytest.mark.parametrize("pattern", PATTERNS, ids=lambda p: p.pattern[:30])
def test_patterns_are_linear(pattern) -> None:  # noqa: ANN001
    for text in DEGENERATE:
        started = time.perf_counter()
        list(pattern.finditer(text))
        assert time.perf_counter() - started < 0.2


def test_helpers_are_fast_on_degenerate_text() -> None:
    h = _hierarchy()
    for text in DEGENERATE:
        for call in (
            lambda: sh.reportable_segments_from_text("three reportable segments: " + text),
            lambda: sh.build_hierarchy([text * 3]),
            lambda: sh.names_in(text, h),
            lambda: sh.scope_finding(text, h),
            lambda: pd.rekey_for_duration("2026-Q3", text[:600], [text]),
            lambda: cc.check_hierarchy(text[:1200], h),
            lambda: cc.metric_scope(text[:1200], [cc.ClaimSource("a", text, None, None)], h),
        ):
            started = time.perf_counter()
            call()  # noqa: B023
            assert time.perf_counter() - started < 1.0
