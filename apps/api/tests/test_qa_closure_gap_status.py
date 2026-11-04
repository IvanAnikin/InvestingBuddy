"""ADR-086 — gap status vocabulary, text-stated web periods, and claim checks.

Every rule, each QA defect as a regression on a synthetic fixture modelled on the real
one, and a replay over stored report payloads (skipped unless
``QA_REPLAY_REPORTS_DIR`` points at a directory of ``<NAME>/report.json``). The bar is
0 false closes and 0 false withholds.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import research_fields as rf
from app.services.agents import investigator as inv
from app.services.council_v2.inputs import FindingRef, GapRef
from app.services.ledger import claim_checks as cc
from app.services.ledger import claim_period as cp
from app.services.ledger import report_invariants as ri
from app.services.ledger import store as ledger
from app.services.ledger.canonical_state import build_canonical_state
from app.services.llm.citation_checker import check_and_sanitize
from app.services.llm.schemas import (
    AgentImplication,
    AgentKeyPoint,
    CouncilAgentOutput,
    EvidenceItem,
)
from app.services.pipeline import gap_reconciliation as gr
from app.services.sources.document_period import statement_reporting_periods

ISSUER = ("issuer_filing",)
AS_OF = date(2026, 11, 1)


def _finding(fid: str, statement: str, **kw) -> gr.FindingFacts:  # noqa: ANN003
    fields, project = gr.finding_fields(statement)
    return gr.FindingFacts(
        finding_id=fid,
        statement=statement,
        fields=kw.pop("fields", fields),
        project=kw.pop("project", project),
        source_kinds=kw.pop("source_kinds", ISSUER),
        stated_period=kw.pop("stated_period", gr.text_period(statement)),
        **kw,
    )


def _gap(description: str, **kw) -> gr.GapFacts:  # noqa: ANN003
    return gr.GapFacts(
        gap_id=kw.pop("gap_id", "g1"),
        gap_type=kw.pop("gap_type", ledger.GAP_EVIDENCE_UNAVAILABLE),
        description=description,
        **kw,
    )


def _one(gap: gr.GapFacts, findings: list[gr.FindingFacts], **kw) -> gr.GapVerdict:  # noqa: ANN003
    (verdict,) = gr.reconcile([gap], findings, as_of=kw.pop("as_of", AS_OF), **kw)
    return verdict


# ── 1. Gap status vocabulary ───────────────────────────────────────────────── #


class TestGapStatus:
    def test_vocabulary(self) -> None:
        assert gr.GAP_STATUSES == (
            "OPEN", "PARTIALLY_CLOSED", "CLOSED", "SUPERSEDED", "NONCOMPARABLE",
            "EXTRACTION_FAILED",
        )

    def test_a_matching_primary_finding_closes(self) -> None:
        verdict = _one(_gap("FY2025 revenue was not acquired"),
                       [_finding("f1", "Revenue of US$50m", period_key="FY2025")])
        assert verdict.gap_status == gr.GAP_STATUS_CLOSED
        assert verdict.to_dict()["gap_status"] == "CLOSED"

    def test_no_finding_is_open(self) -> None:
        verdict = _one(_gap("FY2025 revenue was not acquired"), [])
        assert verdict.gap_status == gr.GAP_STATUS_OPEN

    def test_another_entitys_fact_never_closes_the_subjects_gap(self) -> None:
        peer = _finding(
            "f1", "Peer Resources (a peer, not the company) reported revenue of US$300m "
                  "for FY2025.", period_key="FY2025")
        verdict = _one(_gap("FY2025 revenue was not acquired"), [peer])
        assert verdict.gap_status == gr.GAP_STATUS_OPEN
        assert verdict.finding_ids == []

    def test_a_forecast_never_closes_a_historical_gap(self) -> None:
        forecast = _finding("f1", "The company expects revenue of US$500m.")
        verdict = _one(_gap("No revenue figure in evidence."), [forecast])
        assert verdict.gap_status == gr.GAP_STATUS_NONCOMPARABLE
        assert gr.REASON_FORWARD_LOOKING in verdict.reasons

    def test_a_noun_target_is_not_a_forecast(self) -> None:
        reasons = gr._forward_reasons(
            _finding("f1", "Revenue of US$50m came from 25 gene targets."),
            "metric:revenue", "No revenue figure")
        assert reasons == []

    def test_a_segment_fact_never_closes_a_group_gap(self) -> None:
        seg = _finding("f1", "Wealth revenue was US$5bn", period_key="FY2025",
                       scope_key="segment:wealth")
        verdict = _one(_gap("No group revenue for FY2025 in evidence."), [seg])
        assert verdict.gap_status == gr.GAP_STATUS_PARTIALLY_CLOSED
        assert gr.REASON_SCOPE_DIFFERS in verdict.reasons

    def test_an_unscoped_finding_naming_a_segment_never_closes_a_group_gap(self) -> None:
        seg = _finding("f1", "Wealth segment revenue was US$5bn", period_key="FY2025")
        verdict = _one(_gap("FY2025 revenue was not acquired"), [seg])
        assert verdict.gap_status != gr.GAP_STATUS_CLOSED

    def test_a_group_fact_never_closes_a_segment_gap(self) -> None:
        group = _finding("f1", "Revenue was US$50bn", period_key="FY2025", scope_key="group")
        verdict = _one(_gap("No Wealth segment revenue for FY2025 in evidence."), [group])
        assert verdict.gap_status == gr.GAP_STATUS_OPEN

    def test_a_later_balance_supersedes_an_older_balance_gap(self) -> None:
        later = _finding("f1", "Cash and cash equivalents of US$45m.",
                         period_key="2026-09-30", published_at=date(2026, 10, 25))
        verdict = _one(_gap("No cash balance at 30 June 2026 in evidence."), [later])
        assert verdict.gap_status == gr.GAP_STATUS_SUPERSEDED
        assert verdict.superseded_by_finding_id == "f1"
        assert verdict.reasons == [gr.REASON_SUPERSEDED_BY_LATER]

    def test_an_older_balance_never_supersedes(self) -> None:
        older = _finding("f1", "Cash and cash equivalents of US$45m.",
                         period_key="2026-03-31", published_at=date(2026, 4, 25))
        verdict = _one(_gap("No cash balance at 30 June 2026 in evidence."), [older])
        assert verdict.gap_status == gr.GAP_STATUS_PARTIALLY_CLOSED
        assert gr.REASON_OLDER_PERIOD in verdict.reasons

    def test_a_later_flow_figure_is_only_partial(self) -> None:
        later = _finding("f2", "Revenue of US$50m", period_key="FY2025")
        verdict = _one(_gap("FY2023 revenue was not acquired"), [later])
        assert verdict.gap_status == gr.GAP_STATUS_PARTIALLY_CLOSED
        assert gr.REASON_LATER_PERIOD in verdict.reasons

    def test_an_undated_balance_is_noncomparable(self) -> None:
        undated = _finding("f1", "Cash and cash equivalents of US$45m.")
        verdict = _one(_gap("No cash balance in evidence."), [undated])
        assert verdict.gap_status == gr.GAP_STATUS_NONCOMPARABLE
        assert gr.REASON_NONCOMPARABLE_ONLY in verdict.reasons

    def test_a_held_unread_document_is_extraction_failed(self) -> None:
        gap = _gap("FY2025 revenue was not acquired",
                   knowledge_state=ledger.UNRESOLVED_ACQUIRED_NOT_READ)
        verdict = _one(gap, [])
        assert verdict.gap_status == gr.GAP_STATUS_EXTRACTION_FAILED
        assert gr.REASON_HELD_NOT_READ in verdict.reasons
        gap = _gap("FY2025 revenue was not acquired",
                   unresolved_reason=ledger.UNRESOLVED_ACQUIRED_NOT_READ)
        assert _one(gap, []).gap_status == gr.GAP_STATUS_EXTRACTION_FAILED
        # A finding that answers it wins: the status is about the answer, not the read.
        answered = _one(gap, [_finding("f1", "Revenue of US$50m", period_key="FY2025")])
        assert answered.gap_status == gr.GAP_STATUS_CLOSED

    def test_a_held_unread_document_never_supersedes_its_gap(self) -> None:
        doc = gr.DocumentFacts(kind=rf.DOC_ANNUAL, ref="AR")
        gap = _gap("The annual report could not be fetched",
                   gap_type=ledger.GAP_SOURCE_UNREACHABLE)
        assert _one(gap, [], documents=[doc]).gap_status == gr.GAP_STATUS_SUPERSEDED
        unread = _gap("The annual report could not be fetched",
                      gap_type=ledger.GAP_SOURCE_UNREACHABLE,
                      knowledge_state=ledger.UNRESOLVED_ACQUIRED_NOT_READ)
        assert _one(unread, [], documents=[doc]).gap_status == gr.GAP_STATUS_EXTRACTION_FAILED

    def test_metric_matching_is_domain_blind(self) -> None:
        """LIN2-1: a cash figure recorded under the catalysts domain still answers."""
        f32 = _finding(
            "f32", "Quarter ended 30 Sep 2026: cash and cash equivalents $A45,087k, down "
                   "from $A107,936k at prior quarter end.",
            source_kinds=("issuer_filing", "secondary_web"), scope_key="group",
            published_at=date(2026, 10, 25))
        gap = _gap("No post-placement Lindian cash balance, capex commitment schedule or "
                   "facility drawdown status in evidence.")
        verdict = _one(gap, [f32], as_of=date(2026, 11, 2))
        assert verdict.gap_status == gr.GAP_STATUS_PARTIALLY_CLOSED
        assert verdict.finding_ids == ["f32"]

    def test_gap_ref_carries_the_status(self) -> None:
        def ref(status: str | None) -> GapRef:
            return GapRef(gap_id="g1", gap_type="evidence_unavailable", description="x",
                          why_it_matters=None, status="open", closable=True,
                          gap_status=status)
        assert ref("NONCOMPARABLE").to_dict()["gap_status"] == "NONCOMPARABLE"
        assert "gap_status" not in ref(None).to_dict()


# ── 2. Web findings: a text-stated period ──────────────────────────────────── #


class TestTextStatedPeriod:
    def test_dollar_a_is_australian_dollars(self) -> None:
        text = "Quarter ended 30 Sep 2026: cash and cash equivalents $A45,087k."
        assert "metric:cash" in rf.fields_stated(text)

    def test_text_period_reads_a_quarter_end(self) -> None:
        assert gr.text_period("Quarter ended 30 Sep 2026: cash of $A45,087k.") == "2026-09-30"
        assert gr.text_period("As of September 30, 2026, cash was $8.0 billion.") == (
            "2026-09-30")
        # The ADR-086 reader adds only non-forward periods (a horizon is no period).
        assert cp.single_period("Cash runway into H2 2028.") is None

    def test_one_reported_period(self) -> None:
        stated = statement_reporting_periods(
            "Branded checkout volume grew 2% in Q2 2026, versus BNPL +26%.",
            as_of=date(2026, 9, 1))
        assert stated == ["2026-Q2"]

    @pytest.mark.parametrize("text", [
        "The company expects Q4 2026 revenue of $9bn.",
        "Cash runway into H2 2028.",
    ])
    def test_a_forecast_is_not_a_reporting_period(self, text: str) -> None:
        assert statement_reporting_periods(text, as_of=date(2026, 10, 27)) == []

    def test_no_date_no_period(self) -> None:
        assert statement_reporting_periods("Revenue grew 2% in Q2 2026.", as_of=None) == []

    def test_investigator_uses_it_only_for_dated_web_evidence(self) -> None:
        text = "Branded checkout volume grew 2% in Q2 2026, versus BNPL +26%."
        web = inv._Evidence(citation_id="w1", kind="web", text="t", web=True,
                            published_at=date(2026, 9, 1))
        assert inv._text_stated_period(text, ["w1"], [web]) == "2026-Q2"
        undated = inv._Evidence(citation_id="w2", kind="web", text="t", web=True)
        assert inv._text_stated_period(text, ["w2"], [undated]) is None
        filing = inv._Evidence(citation_id="e1", kind="filing", text="t",
                               published_at=date(2026, 9, 1))
        assert inv._text_stated_period(text, ["e1"], [filing]) is None
        two = "Revenue grew 2% in Q2 2026 after 5% in Q1 2026."
        assert inv._text_stated_period(two, ["w1"], [web]) is None
        assert inv.PERIOD_BASIS_TEXT_STATED == "text_stated"

    def test_finding_ref_exposes_the_basis_only_when_text_stated(self) -> None:
        def row(period_type: str | None) -> SimpleNamespace:
            return SimpleNamespace(
                id="f1", statement="s", mechanism=None, direction=None, confidence=None,
                evidence_ids_json=[], calculation_ids_json=[], period_key="2026-Q2",
                scope_key=None, originating_role=None, verification_status="verified",
                period_type=period_type)
        assert FindingRef.from_row(row("text_stated")).to_dict()["period_basis"] == (
            "text_stated")
        assert "period_basis" not in FindingRef.from_row(row(None)).to_dict()
        assert "period_basis" not in FindingRef.from_row(row("annual")).to_dict()


# ── 3. Claim checks ─────────────────────────────────────────────────────────── #


S = cc.ClaimSource


class TestClaimChecks:
    def test_jpm6_half_year_presented_as_a_quarter(self) -> None:
        verdict = cc.check_claim(
            "Investment banking revenue for Q2 2026 was $7.0 billion, up 42%.",
            [S("E7", "Investment banking revenue for the six months ended June 30, 2026 "
                     "was $7.0 billion, up 42%.")])
        assert verdict is not None and verdict.rule == cc.RULE_CLAIM_PERIOD

    def test_the_right_quarter_passes(self) -> None:
        assert cc.check_claim(
            "Investment banking revenue for Q2 2026 was $3.9 billion.",
            [S("E7", "Investment banking revenue for the three months ended June 30, 2026 "
                     "was $3.9 billion.")]) is None

    def test_jpm6_a_segment_figure_presented_as_the_firms(self) -> None:
        verdict = cc.check_claim(
            "Firm noninterest revenue was $5.2 billion in Q2 2026.",
            [S("F9", "Corporate noninterest revenue was $5.2 billion in Q2 2026.",
               period="2026-Q2", scope="segment:corporate")])
        assert verdict is not None and verdict.rule == cc.RULE_CLAIM_SCOPE
        assert "corporate" in verdict.reason

    def test_a_segment_figure_naming_its_segment_passes(self) -> None:
        assert cc.check_claim(
            "Corporate noninterest revenue was $5.2 billion in Q2 2026.",
            [S("F9", "Corporate noninterest revenue was $5.2 billion in Q2 2026.",
               period="2026-Q2", scope="segment:corporate")]) is None

    def test_jpm6_a_derived_roe_is_labelled(self) -> None:
        derived = S("F3", "Return on equity of 15.2% (platform calculation).",
                    period="FY2025", derived=True, ratio_keys=frozenset({"roe"}))
        verdict = cc.check_claim("ROE was 15.2% in FY2025.", [derived])
        assert verdict is not None and verdict.rule == cc.RULE_DERIVED_AS_REPORTED
        assert verdict.replacement == "ROE (platform-derived) was 15.2% in FY2025."

    def test_jpm6_a_derived_roe_presented_as_reported_is_withheld(self) -> None:
        derived = S("F3", "Return on equity of 15.2%.", derived=True,
                    ratio_keys=frozenset({"roe"}))
        verdict = cc.check_claim("The bank reported ROE of 15.2%.", [derived])
        assert verdict is not None and verdict.replacement is None

    def test_a_reported_roe_passes(self) -> None:
        assert cc.check_claim("The bank reported ROE of 15.2%.",
                              [S("E1", "Return on equity was 15.2%.")]) is None

    def test_pypl5_annual_cash_dated_to_a_quarter_end(self) -> None:
        verdict = cc.check_claim(
            "Cash and cash equivalents were $8.0 billion as of September 30, 2026.",
            [S("f1", "At FY2025 group cash was USD 8,049m.", period="FY2025")])
        assert verdict is not None and verdict.rule == cc.RULE_CLAIM_PERIOD

    def test_pypl2_5_q2_growth_spliced_into_q3(self) -> None:
        verdict = cc.check_claim(
            "Q3 2026 revenue grew 2% to USD 8.58 billion, with BNPL volume +26%.",
            [S("a", "Q3 2026 revenue grew 2% YoY to $8.58 billion.", period="2026-Q3"),
             S("b", "Branded checkout grew 2% in Q2 2026, versus BNPL +26%.",
               period="2026-Q2")])
        assert verdict is not None and verdict.rule == cc.RULE_CLAIM_PERIOD
        assert "+26%" in verdict.reason

    def test_a_comparative_clause_keeps_its_own_period(self) -> None:
        assert cc.check_claim(
            "Q3 2026 revenue was $8.6 billion, compared with $8.0 billion in the prior "
            "year.",
            [S("a", "Q3 2026 revenue was $8.6 billion.", period="2026-Q3"),
             S("b", "Q3 2025 revenue was $8.0 billion.", period="2025-Q3")]) is None

    def test_a_guidance_figure_is_not_period_checked(self) -> None:
        """ANTO replay: FY guidance cited from a Q2 report, beside a Q3 remark."""
        assert cc.check_claim(
            "2026 cost guidance was raised to $2.60-2.70/lb, though Q3 2026 costs "
            "improved.",
            [S("x", "Q2 2026: cost guidance raised to $2.60-2.70/lb.", period="2026-Q2")],
        ) is None

    def test_a_bare_integer_is_not_a_figure(self) -> None:
        """ANTO2 replay: "section 172" is not a figure."""
        assert cc.figures("section 172 and Note 23") == []
        assert cc.check_claim(
            "Section 172 statements; financing was not in place at 31 December 2023.",
            [S("x", "Section 172 statement for 2024.", period="FY2024")]) is None

    def test_percentages_are_figures(self) -> None:
        assert [f.kind for f in cc.figures("BNPL +26% and Venmo 14.5%")] == ["pct", "pct"]

    def test_an_unknown_source_period_never_withholds(self) -> None:
        assert cc.check_claim("Revenue for Q2 2026 was $3.9 billion.",
                              [S("x", "Revenue was $3.9 billion.")]) is None


# ── 3b. The enforcer: chair key points ─────────────────────────────────────── #


def _v3(points: list[dict[str, str]], findings: list[dict[str, object]]) -> dict[str, object]:
    return {"findings": findings, "chair": {"key_points": points}, "gaps": []}


class TestEnforcer:
    FID_Q3 = "4a64ea90-1623-4f61-a9a9-95887a2e2f4c"
    FID_Q2 = "e9e020d1-bd0a-4156-8771-5dad61781f7c"

    def _findings(self) -> list[dict[str, object]]:
        return [
            {"finding_id": self.FID_Q3, "statement": "Q3 2026 revenue grew 2% YoY to $8.58 "
             "billion.", "period_key": "2026-Q3", "source_kinds": ["secondary_web"]},
            {"finding_id": self.FID_Q2, "statement": "Branded checkout grew 2% in Q2 2026, "
             "versus BNPL +26% and Venmo TPV +14%.", "period_key": "2026-Q2",
             "source_kinds": ["secondary_web"]},
        ]

    def test_pypl2_5_the_spliced_chair_point_is_withheld_with_its_reason(self) -> None:
        text = (f"Q3 2026 revenue grew 2% YoY to USD 8.58 billion, with BNPL (+26%) "
                f"(finding_id: {self.FID_Q3}, {self.FID_Q2}).")
        v3 = _v3([{"text": text, "finding_id": self.FID_Q3}], self._findings())
        result = ri.enforce(v3, build_canonical_state(v3, as_of="2026-11-02"))
        hits = [v for v in result.violations if v.rule == ri.RULE_CLAIM_PERIOD]
        assert len(hits) == 1
        assert hits[0].action == "withheld" and hits[0].path == "chair.key_points[0]"
        assert str(result.v3["chair"]["key_points"][0]["text"]).startswith("[Withheld")

    def test_a_faithful_chair_point_is_kept(self) -> None:
        text = f"Q3 2026 revenue grew 2% YoY to $8.58 billion (finding_id: {self.FID_Q3})."
        v3 = _v3([{"text": text, "finding_id": self.FID_Q3}], self._findings())
        result = ri.enforce(v3, build_canonical_state(v3, as_of="2026-11-02"))
        assert result.v3["chair"]["key_points"][0]["text"] == text
        assert not [v for v in result.violations if v.rule in cc.CLAIM_RULES]

    def test_a_derived_ratio_is_labelled_in_place(self) -> None:
        fid = "11111111-2222-3333-4444-555555555555"
        v3 = _v3([{"text": "ROE was 15.2% in FY2025.", "finding_id": fid}],
                 [{"finding_id": fid, "statement": "Return on equity 15.2% for FY2025.",
                   "period_key": "FY2025", "source_kinds": ["platform_calculation"]}])
        result = ri.enforce(v3, build_canonical_state(v3, as_of="2026-11-02"))
        assert result.v3["chair"]["key_points"][0]["text"] == (
            "ROE (platform-derived) was 15.2% in FY2025.")

    def test_lin6_a_disagreement_id_not_in_the_ledger_is_withheld(self) -> None:
        state = {"disagreements": {"unresolved_ids": ["abcdef12-0000-0000-0000-000000000000"],
                                   "unresolved": 1, "total": 1}}
        hit = ri.check_sentence("The council left disagreement 99999999 unresolved.",
                                state, site=ri.SITE_CHAIR)
        assert hit is not None and hit.rule == ri.RULE_UNKNOWN_DISAGREEMENT_ID
        assert ri.check_sentence("The council left disagreement abcdef12 unresolved.",
                                 state, site=ri.SITE_CHAIR) is None

    def test_the_v2_primary_document_line_derives_from_the_ledger(self) -> None:
        state = {"documents": {"issuer_documents_held": 2,
                               "newest_issuer_document": {"date": "2026-03-20",
                                                          "form": "20-F"}},
                 "current_period": {"extraction_failed": False}}
        content = {"thin_evidence_state": {"missing_evidence_groups": [{
            "category": ri.PRIMARY_DOC_GROUP_CATEGORY, "status": "missing",
            "detail": "No issuer annual report or equivalent primary document has been "
                      "ingested and parsed."}]}}
        out, violations = ri.enforce_content(content, state)
        assert out is not None
        group = out["thin_evidence_state"]["missing_evidence_groups"][0]
        assert group["detail"].startswith("The platform holds 2 issuer primary document(s)")
        assert "No issuer annual report" not in json.dumps(out)
        assert [v.rule for v in violations if v.rule == ri.RULE_PRIMARY_DOC_GROUP]

    def test_with_no_document_held_the_v2_line_stands(self) -> None:
        assert ri.primary_document_group({"documents": {"issuer_documents_held": 0}}) is None


# ── 3c. Legacy council: checked against its evidence items at generation ──── #


class TestLegacyCouncil:
    def _check(self, claim: str, item: EvidenceItem) -> tuple[CouncilAgentOutput, list[str]]:
        out = CouncilAgentOutput(
            agent_name="bear_case",
            key_points=[AgentKeyPoint(claim=claim, citation_ids=[item.id])],
            implications=[AgentImplication(statement=claim, citation_ids=[item.id])],
        )
        return check_and_sanitize(out, {item.id}, {item.id: item})

    def test_jpm6_a_half_year_figure_called_a_quarter_is_dropped(self) -> None:
        item = EvidenceItem(
            id="E1", source_tier="T1", source_type="company_filing", period="2026-H1",
            excerpt="Investment banking revenue for the six months ended June 30, 2026 was "
                    "$7.0 billion.")
        out, issues = self._check("Investment banking revenue for Q2 2026 was $7.0 billion.",
                                  item)
        assert out.key_points == [] and out.implications == []
        assert any(cc.RULE_CLAIM_PERIOD in i for i in issues)
        assert all("Investment banking revenue for Q2" not in i for i in issues)

    def test_a_derived_metric_is_labelled(self) -> None:
        item = EvidenceItem(
            id="E2", source_tier="T6_model_estimate", source_type="derived_financial_metric",
            content_tier="T6_model_estimate", excerpt="return_on_equity_pct=15.2%",
            fields_supported=["return_on_equity_pct"])
        out, _issues = self._check("Return on equity was 15.2% for the year.", item)
        assert out.key_points[0].claim == (
            "Return on equity (platform-derived) was 15.2% for the year.")

    def test_a_faithful_claim_is_unchanged(self) -> None:
        item = EvidenceItem(
            id="E3", source_tier="T1", source_type="company_filing", period="2026-Q2",
            excerpt="Revenue for the three months ended June 30, 2026 was $3.9 billion.")
        claim = "Revenue for Q2 2026 was $3.9 billion."
        out, _issues = self._check(claim, item)
        assert [k.claim for k in out.key_points] == [claim]


# ── 4. Linear regexes ───────────────────────────────────────────────────────── #

_DEGENERATE = [" " * 10000, "\n" * 10000, "A-" * 3000, "a," * 5000, "a." * 3000]


@pytest.mark.parametrize("text", _DEGENERATE, ids=["spaces", "newlines", "A-", "a,", "a."])
def test_every_new_pattern_is_linear(text: str) -> None:
    patterns: list[re.Pattern[str]] = [
        cc._FIGURE_RE, cc._UUID_RE, cc._ID_RE, cc._CLAUSE_SPLIT_RE, cc._SOURCE_SPLIT_RE,
        cc._COMPARATIVE_RE, cc._GROUP_WORDS_RE, cc._RATIO_RE, cc._REPORTED_RE,
        cc._DERIVED_WORDS_RE, cc._FORWARD_RE, cc._CLEAN_SCOPE_NAME_RE,
        gr._OTHER_ENTITY_RE, gr._FORWARD_CLAUSE_RE, ri._DISAGREEMENT_ID_RE, rf.MONEY_RE,
    ]
    patterns += [v for k, v in vars(cp).items()
                 if k.endswith("_RE") and isinstance(v, re.Pattern)]
    for pattern in patterns:
        started = time.perf_counter()
        list(pattern.finditer(text))
        assert time.perf_counter() - started < 0.2, pattern.pattern[:60]
    for fn in (lambda: cp.mentions(text), lambda: cc.figures(text),
               lambda: cc.check_claim(text[:1200], [S("x", text)]),
               lambda: gr.text_period(text),
               lambda: statement_reporting_periods(text, as_of=AS_OF)):
        started = time.perf_counter()
        fn()
        assert time.perf_counter() - started < 0.2


def test_a_degenerate_claim_is_bounded() -> None:
    started = time.perf_counter()
    cc.check_claim("$1 " * 400, [S("x", "$1 " * 1300)])
    assert time.perf_counter() - started < 1.0


# ── 5. Replay over stored reports ──────────────────────────────────────────── #

_REPORTS = os.environ.get("QA_REPLAY_REPORTS_DIR")


@pytest.mark.skipif(not _REPORTS or not Path(_REPORTS).is_dir(),
                    reason="set QA_REPLAY_REPORTS_DIR to replay stored payloads")
def test_replay_over_stored_reports_has_no_false_closes() -> None:
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "qa_replay_gap_status.py"
    spec = importlib.util.spec_from_file_location("qa_replay_gap_status", script)
    assert spec is not None and spec.loader is not None
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    assert _REPORTS is not None
    for path in sorted(Path(_REPORTS).glob("*/report.json")):
        row = replay.replay_report(json.loads(path.read_text()))
        for gid, verdict in row["gaps"].items():
            assert verdict["gap_status"] in gr.GAP_STATUSES
            if verdict["gap_status"] in (gr.GAP_STATUS_CLOSED, gr.GAP_STATUS_SUPERSEDED):
                assert row["stored"].get(gid) in ("closed", "superseded"), (path, gid)
