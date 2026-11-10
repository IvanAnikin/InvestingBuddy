"""ADR-091 — rerun closure batch H2: status lines, period inheritance, comparability,
stated scope, sector applicability, commercial claims, peer context, fiscal calendars
and phantom chair conflicts.

One regression per defect, on synthetic fixtures modelled on the stored payloads, and a
linear-time check of every new pattern. No network; no issuer names in app code.
"""

from __future__ import annotations

import gc
import time
from datetime import date

import pytest

from app.services import commercial_claims as ccl
from app.services.agents import investigator as inv
from app.services.director import roles
from app.services.ledger import canonical_state as cs
from app.services.ledger import claim_checks as cc
from app.services.ledger import claim_period as cp
from app.services.ledger import period_duration as pdur
from app.services.ledger import report_invariants as ri
from app.services.ledger import segment_hierarchy as sh
from app.services.ledger import store as ledger
from app.services.ledger.canonical_state import LedgerTally, build_canonical_state
from app.services.pipeline import gap_reconciliation as gr
from app.services.pipeline import professional_research as pr
from app.services.playbooks import industries

AS_OF = date(2026, 11, 5)
ISSUER = ("issuer_filing",)

A = "06aaab45-a757-402e-8acc-90ba5291d6d8"
B = "82be1948-1806-42c2-8e7b-93a3fbf50fc0"
C = "75c2954b-0329-4ee2-a022-bed224de9833"
MISSING = "0badf00d-0000-4000-8000-000000000000"


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


def _tally(**kw) -> LedgerTally:  # noqa: ANN003
    base = dict(findings=3, verified=0, primary_source=3, gaps_open=4)
    base.update(kw)
    return LedgerTally(**base)  # type: ignore[arg-type]


# ── 1. Status lines come from the canonical state ─────────────────────────── #


class TestStatusLines:
    def _v3(self) -> dict:
        return {
            "consumption": {"web_search_calls": 1, "web_stage": {"web_search_calls": 33}},
            # ADR-095: the stage's searches are read from its own query record.
            "web_context": {"provider": "tavily", "queries": {
                "planned": 33, "executed": 33, "failed": 0, "not_issued": 0}},
            "financial_statements_state": {
                "reporting_periods": {"latest_annual": "FY2025", "latest_interim": None,
                                      "latest_quarter": "Q3 2026",
                                      "latest_current_period": "Q3 2026"},
                "slots": {"revenue": {"xbrl_concept": "Revenues"}, "net_income": {}},
                "facts_read": 12,
            },
            "primary_document_coverage": {"documents": [
                {"document_type": "annual_report", "chunks": 300,
                 "published_at": "2026-02-13", "extraction_status": "extracted"},
                {"document_type": "annual_report", "chunks": 300,
                 "extraction_status": "extracted"},
                {"document_type": "results_release", "chunks": 20,
                 "published_at": "2026-10-14", "extraction_status": "extracted"},
                {"document_type": "interim_report", "chunks": 90,
                 "published_at": "2026-11-01", "extraction_status": "extracted"},
            ]},
            "loop": {"gaps_open": 61},
            "gaps": [{"gap_id": f"g{i}", "status": "open"} for i in range(3)],
        }

    def test_status_block_is_derived_once(self) -> None:
        state = build_canonical_state(self._v3(), tally=_tally(gaps_open=55), as_of=AS_OF)
        status = state["status"]
        assert status["web_searches"] == {
            "total": 34, "web_stage": 33, "investigators": 1,
            # ADR-095 — what the executed searches returned.
            "planned": 34, "succeeded": 33, "failed": 0, "outcome_unknown": 1}
        # The undated duplicate of the dated annual report is one document.
        assert status["documents_read"] == 3
        assert status["latest_interim"] == {"label": "Q3 2026", "basis": "statement_facts"}
        assert status["statement_facts"]["sec_tagged_slots"] == 1
        assert status["results_releases_read"] == 1
        assert status["gaps"] == {"open": 55, "listed": 3}

    def test_a_contradicting_status_line_is_a_violation(self) -> None:
        v3 = self._v3()
        state = build_canonical_state(v3, tally=_tally(gaps_open=55), as_of=AS_OF)
        rules = {(v.rule, v.path) for v in ri.check(v3, state)}
        assert (ri.RULE_STATUS_LINE, "consumption.web_search_calls") in rules
        assert (ri.RULE_STATUS_LINE, "loop.gaps_open") in rules
        assert (ri.RULE_STATUS_LINE,
                "financial_statements_state.reporting_periods.latest_interim") in rules

    def test_v2_not_sourced_lines_are_repaired_from_the_state(self) -> None:
        state = build_canonical_state(self._v3(), tally=_tally(gaps_open=55), as_of=AS_OF)
        content = {
            "data_availability_summary": {"fundamentals_note": "Company fundamentals not "
                                          "sourced from a filing."},
            "evidence_channels": {"channels": [
                {"channel": "regulator_structured_facts", "detail": "not sourced"},
                {"channel": "issuer_newsroom", "detail": "not sourced"},
            ]},
            "summary": "There are 9 blocking research gaps.",
        }
        out, violations = ri.enforce_content(content, state)
        assert out is not None
        channels = out["evidence_channels"]["channels"]
        assert all(c["available"] for c in channels)
        assert "9 required report field(s) not yet filled" in out["summary"]
        assert {v.rule for v in violations} == {ri.RULE_STATUS_LINE}

    def test_a_line_that_agrees_is_left_alone(self) -> None:
        v3 = self._v3()
        v3["consumption"] = {"web_search_calls": 0}
        v3.pop("web_context")  # no web stage ran: the run made 0 searches
        v3["loop"] = {"gaps_open": 55}
        v3["financial_statements_state"]["reporting_periods"]["latest_interim"] = "Q3 2026"
        state = build_canonical_state(v3, tally=_tally(gaps_open=55), as_of=AS_OF)
        assert [v for v in ri.check(v3, state) if v.rule == ri.RULE_STATUS_LINE] == []


# ── 2. An inherited forward period loses; "2025" agrees with "FY2025" ──────── #


class TestInheritedPeriod:
    STATEMENT = ("H1 2026 operating cash used USD 29,694k (H1 2025: 27,470k); cash "
                 "USD 35,590k at 30 Jun 2026")

    def test_a_forward_inherited_period_loses_to_the_stated_one(self) -> None:
        got = inv._stated_over_forward("2027-Q4", self.STATEMENT, date(2026, 9, 11), AS_OF)
        assert got == "2026-H1"

    def test_an_ended_inherited_period_stands(self) -> None:
        assert inv._stated_over_forward("2026-H1", self.STATEMENT,
                                        date(2026, 9, 11), AS_OF) is None

    def test_no_single_stated_period_keeps_the_key(self) -> None:
        assert inv._stated_over_forward("2027-Q4", "Cash runway into Q4 2027.",
                                        date(2026, 9, 11), AS_OF) is None

    def test_bare_and_fy_year_agree_for_a_calendar_year(self) -> None:
        assert inv._agreement_key("2025", calendar_fiscal_year=True) == "FY2025"
        assert inv._agreement_key("FY2025", calendar_fiscal_year=None) == "FY2025"
        # Known non-calendar: a bare year may be the calendar reading.
        assert inv._agreement_key("2025", calendar_fiscal_year=False) == "2025"

    def test_mixed_spellings_inherit_one_period(self) -> None:
        evidence = [
            inv._Evidence(citation_id="e1", kind="fact", text="x", period_key="FY2025"),
            inv._Evidence(citation_id="e2", kind="chunk", text="y", period_key="2025"),
        ]
        period, _scope, conflicts = inv._inherited(["e1", "e2"], evidence,
                                                   calendar_fiscal_year=True)
        assert period in ("FY2025", "2025")
        assert conflicts == []


# ── 3. Another scope / a product line is NONCOMPARABLE ─────────────────────── #


class TestComparability:
    def test_scope_differs_is_noncomparable(self) -> None:
        assert gr.is_noncomparable([gr.REASON_SCOPE_DIFFERS])
        seg = _finding("f1", "Wealth revenue was US$5bn", period_key="FY2025",
                       scope_key="segment:wealth")
        verdict = _one(_gap("No group revenue for FY2025 in evidence."), [seg])
        assert verdict.gap_status == gr.GAP_STATUS_NONCOMPARABLE

    def test_an_unscoped_product_line_figure_is_not_the_groups(self) -> None:
        line = _finding("f1", "Wallet revenue up ~$1.1bn and Ledgerpay up ~$230m in "
                              "FY2025.", period_key="FY2025")
        words = gr.subject_words("Example Payments Holdings", "EXPH")
        assert gr._sub_line_reasons(line, words, None) == [gr.REASON_SCOPE_DIFFERS]
        verdict = _one(_gap("No FY2025 revenue in evidence."), [line],
                       subject_name="Example Payments Holdings", subject_ticker="EXPH")
        assert verdict.gap_status != gr.GAP_STATUS_CLOSED

    def test_the_groups_own_figure_is_not_a_product_line(self) -> None:
        words = gr.subject_words("Example Payments Holdings", "EXPH")
        own = _finding("f1", "Net revenue of $31.8bn for FY2025, up 4%.",
                       period_key="FY2025")
        assert gr._sub_line_reasons(own, words, None) == []
        headline = _finding("f2", "Total Revenue was $31.8bn in FY2025.",
                            period_key="FY2025")
        assert gr._sub_line_reasons(headline, words, None) == []

    def test_an_older_period_of_the_same_metric_and_scope_is_noncomparable(self) -> None:
        # ADR-094 (spec §10): FY2024 revenue does not partly answer "no FY2025 revenue".
        older = _finding("f1", "Revenue was US$40m for FY2024.", period_key="FY2024")
        verdict = _one(_gap("FY2025 revenue was not acquired"), [older])
        assert verdict.gap_status == gr.GAP_STATUS_NONCOMPARABLE

    def test_an_older_period_still_answers_a_gap_asking_for_history(self) -> None:
        older = _finding("f1", "Revenue was US$40m for FY2024.", period_key="FY2024")
        verdict = _one(_gap("No FY2025 revenue to complete the multi-year trend"), [older])
        assert verdict.gap_status == gr.GAP_STATUS_PARTIALLY_CLOSED


# ── 4. A segment figure is never presented as the whole company ───────────── #


class TestStatedSegment:
    def test_one_named_segment_scopes_the_claim_source(self) -> None:
        assert cc.stated_segment(
            "In the Corporate segment, net revenue was $93m in Q3 2026.") \
            == "segment:corporate"
        src = cc.source_of_finding({"finding_id": "f1", "statement":
                                    "The Asset & Wealth Management segment reported "
                                    "revenue of $6.1bn."})
        assert src is not None and src.scope == "segment:asset & wealth management"

    def test_a_claim_restating_the_segment_figure_as_the_firms_is_withheld(self) -> None:
        source = cc.source_of_finding({"finding_id": "f1", "statement":
                                       "CIB segment (not Group) 3Q26: net income $8.2B, "
                                       "up 20% YoY."})
        assert source is not None and source.scope == "segment:cib"
        verdict = cc.check_claim("Q3 2026 net income rose 20% to $8.2bn.", [source])
        assert verdict is not None and verdict.rule == cc.RULE_CLAIM_SCOPE

    def test_a_claim_spelling_out_the_acronym_names_the_scope(self) -> None:
        source = cc.source_of_finding({"finding_id": "f1", "statement":
                                       "CIB segment (not Group) 3Q26: net income $8.2B, "
                                       "up 20% YoY."})
        assert source is not None
        claim = ("Q3 2026 net income for the Commercial & Investment Bank segment rose 20% "
                 "to $8.2bn.")
        assert cc.check_claim(claim, [source]) is None

    def test_several_or_generic_segments_name_none(self) -> None:
        assert cc.stated_segment("Each reportable segment grew revenue.") is None
        assert cc.stated_segment("Revenue rose in the Retail segment and the Commercial "
                                 "segment.") is None
        assert cc.stated_segment("Revenue was $10bn.") is None


# ── 5. Templates follow sector applicability ──────────────────────────────── #


class TestApplicability:
    def test_bank_skips_resource_style_questions(self) -> None:
        skipped = set(industries.BANK_NOT_MEANINGFUL.questions)
        assert {"growth_pipeline", "operations_and_assets"} <= skipped

    def test_growth_analyst_focus_is_sector_neutral(self) -> None:
        focus = roles.EVENT_ANALYST.focus.lower()
        for word in ("permit", "offtake", "construction"):
            assert word not in focus


# ── 6. Commercial-claims verbs ────────────────────────────────────────────── #


SUBJECT = {"name": "Example Minerals Ltd", "ticker": "EXM"}


class TestCommercialClaims:
    @pytest.mark.parametrize("text", [
        "Example Minerals extracts lithium at its site.",
        "The company processes spodumene concentrate.",
        "Example Minerals ships concentrate to China.",
        "The company is making battery-grade hydroxide.",
        "Example Minerals operates a producing mine in Ontario.",
        "The company owns the Delta hydromet plant in Kazakhstan making MREC.",
    ])
    def test_new_verbs_are_claims(self, text: str) -> None:
        assert ccl.claims_commercial_operation(text, SUBJECT)

    @pytest.mark.parametrize("text", [
        "The company sells shares to fund the project.",
        "The company produces annual reports.",
        "The company's processes are documented.",
        "The company plans a plant which will make hydroxide.",
        "Its customer's plant ships concentrate.",
        "The company is not producing yet.",
    ])
    def test_non_product_objects_and_forward_words_are_not(self, text: str) -> None:
        assert not ccl.claims_commercial_operation(text, SUBJECT)


# ── 7. No peer figure stands as peer data when peers are unavailable ──────── #


class TestPeerContext:
    def test_other_company_figures_are_labelled_not_a_comparable(self) -> None:
        section = {"findings": [
            {"finding_id": "f1", "statement": "Peer Sands (a peer, not the company) "
                                              "realised price $1,236/t."},
            {"finding_id": "f2", "statement": "Example Minerals realised $1,000/t in "
                                              "FY2025."},
            {"finding_id": "f3", "statement": "The lithium market is in surplus."},
            # The subject's own brand, unmarked: never labelled (CFR2 replay).
            {"finding_id": "f4", "statement": "Brandmark expanded its boutique network in "
                                              "2025, with sales up 12%.",
             "source_kinds": ["issuer_filing"]},
            # A competitor list with no quantity (a form name is not a figure).
            {"finding_id": "f5", "statement": "The 20-F names competitors: Alpha, Beta."},
        ]}
        marked = pr.label_noncomparable_peer_figures(section, SUBJECT)
        assert marked == 1
        f1, *rest = section["findings"]
        assert f1["context_only"] is True
        assert f1["context_label"] == pr.NOT_A_COMPARABLE_LABEL
        assert all("context_only" not in f for f in rest)
        assert section["context_findings_labelled"] == 1


# ── 8. Fiscal calendars ───────────────────────────────────────────────────── #


def _march_issuer() -> dict:
    stated = [
        {"finding_id": f"f{i}", "statement": text, "source_kinds": ["issuer_ir"],
         "period_key": key}
        for i, (text, key) in enumerate([
            ("Annual Report and Accounts for the year ended 31 March 2026 published.",
             "FY2026"),
            ("Goodwill test for the year ended 31 March 2024 assumes 1% growth.", None),
            ("Sales for the year ended 31 March 2025 were EUR 21.4bn.", "2025"),
            ("Q1 sales rose 20% in the first quarter ended 30 June 2026.", "2026-Q1"),
        ])
    ]
    return {
        "core_filings": {"annual": None, "skipped": "not an SEC registrant"},
        "core_disclosures": {"state": "venue_not_covered"},
        "findings": stated,
        "financial_statements_state": {"reporting_periods": {
            "latest_annual": "FY2026", "latest_interim": None,
            "latest_quarter": "Q1 2027", "latest_current_period": "Q1 2027"}},
        "primary_document_coverage": {"documents": [
            {"document_type": "annual_report", "chunks": 350, "published_at": "2026-05-22",
             "extraction_status": "extracted"},
            {"document_type": "interim_report", "chunks": 132,
             "published_at": "2025-11-21", "extraction_status": "extracted"},
            {"document_type": "press_release", "chunks": 16,
             "published_at": "2026-07-17", "extraction_status": "extracted"},
        ]},
    }


class TestFiscalCalendar:
    def test_fiscal_period_bounds(self) -> None:
        assert cs.period_bounds("FY2027", fiscal_year_end_month=3) == (
            date(2026, 4, 1), date(2027, 3, 31))
        assert cs.period_bounds("2027-Q1", fiscal_year_end_month=3) == (
            date(2026, 4, 1), date(2026, 6, 30))
        assert cs.period_bounds("Q1 2027", fiscal_year_end_month=6) == (
            date(2026, 7, 1), date(2026, 9, 30))
        assert cs.period_bounds("2027-H1", fiscal_year_end_month=3) == (
            date(2026, 4, 1), date(2026, 9, 30))
        # December or unknown: the calendar reading, unchanged.
        for month in (None, 12):
            assert cs.period_bounds("2026-Q3", fiscal_year_end_month=month) == (
                date(2026, 7, 1), date(2026, 9, 30))
            assert cs.period_bounds("FY2025", fiscal_year_end_month=month) == (
                date(2025, 1, 1), date(2025, 12, 31))

    def test_year_end_stated_in_the_issuers_documents(self) -> None:
        fye = cs.fiscal_year_end(_march_issuer())
        assert fye is not None and fye["month"] == 3
        assert fye["basis"] == "issuer_documents_stated"

    def test_one_mention_or_a_split_is_no_basis(self) -> None:
        v3 = _march_issuer()
        v3["findings"] = v3["findings"][:1]
        assert cs.fiscal_year_end(v3) is None
        v3 = _march_issuer()
        v3["findings"] += [
            {"finding_id": "x1", "statement": "Subsidiary year ended 31 December 2025.",
             "source_kinds": ["issuer_ir"]},
            {"finding_id": "x2", "statement": "Year ended 31 December 2024 figures.",
             "source_kinds": ["issuer_ir"]},
        ]
        assert cs.fiscal_year_end(v3) is None

    def test_a_january_year_end_is_no_basis(self) -> None:
        v3 = {"findings": [
            {"finding_id": f"f{i}", "statement": "for the year ended 31 January 2026",
             "source_kinds": ["issuer_filing"]} for i in range(3)]}
        assert cs.fiscal_year_end(v3) is None

    def test_canonical_state_uses_the_fiscal_calendar_and_parsed_documents(self) -> None:
        state = build_canonical_state(_march_issuer(), as_of=AS_OF)
        docs = state["documents"]
        assert docs["issuer_documents_held"] == 3
        assert docs["issuer_documents_held_basis"] == "document_coverage"
        assert docs["latest_reporting_period"] == "Q1 2027"
        assert docs["latest_reporting_period_end"] == "2026-06-30"
        assert docs["fiscal_year_end"]["month"] == 3

    def test_a_calendar_issuer_is_unchanged(self) -> None:
        v3 = {"findings": [{"finding_id": "f1", "statement": "Revenue for Q3 2026.",
                            "source_kinds": ["issuer_filing"], "period_key": "2026-Q3",
                            "source_published_at": "2026-10-30"}]}
        docs = build_canonical_state(v3, as_of=AS_OF)["documents"]
        assert docs["latest_reporting_period"] == "2026-Q3"
        assert docs["latest_reporting_period_end"] == "2026-09-30"
        assert docs["fiscal_year_end"] is None

    def test_an_inherited_calendar_quarter_takes_the_fiscal_key(self) -> None:
        text = ("Sales up 20% at constant rates for its first quarter ended 30 June 2026.")
        assert inv._fiscal_quarter_restated("2026-Q1", text, date(2025, 11, 21),
                                            AS_OF) == "2027-Q1"
        # A calendar issuer's own key agrees; a quarter of another ordinal is not rekeyed.
        assert inv._fiscal_quarter_restated(
            "2026-Q2", "Q2 sales for the quarter ended 30 June 2026 rose 4%.",
            date(2026, 7, 30), AS_OF) is None
        assert inv._fiscal_quarter_restated("FY2026", text, None, AS_OF) is None
        assert inv._fiscal_quarter_restated("2026-Q1", "Sales rose 20%.", None,
                                            AS_OF) is None


# ── 9. Phantom chair conflicts ────────────────────────────────────────────── #


class TestPhantomConflict:
    def test_net_cash_not_net_debt_is_no_absence(self) -> None:
        sign = _finding(A, "Net cash of EUR 8,496m at 31 March 2026, i.e. the Group "
                           "reports a net cash position, not net debt.")
        cash = _finding(B, "Net cash was EUR 8,496m at 31 March 2026.")
        assert gr.contradicted_absences([sign, cash]) == []

    def test_a_source_label_negation_is_no_absence(self) -> None:
        labelled = _finding(A, "[company says; not from a filing] At 31 March 2024, the "
                               "Group held EUR 10,710m as cash at bank.")
        cash = _finding(B, "Cash at bank was EUR 10,710m at 31 March 2024.")
        assert gr.contradicted_absences([labelled, cash]) == []

    def test_a_real_denial_is_still_paired(self) -> None:
        absent = _finding(A, "No interim or quarterly revenue appears in the evidence.")
        stated = _finding(B, "Q2 2026 net sales were $4,289.0 million.",
                          period_key="2026-Q2")
        pairs = gr.contradicted_absences([absent, stated])
        assert [(p.finding_a_id, p.finding_b_id) for p in pairs] == [(A, B)]
        annual_only = _finding(A, "Revenue is reported annually, not quarterly.")
        assert gr.contradicted_absences([annual_only, stated]) != []

    def _v3(self, point_text: str, point_id: str) -> dict:
        return {
            "findings": [
                {"finding_id": A, "statement": "The Group reports net cash of EUR 8,496m.",
                 "source_kinds": ["issuer_ir"]},
                {"finding_id": B, "statement": "Net cash was EUR 8,496m.",
                 "source_kinds": ["issuer_ir"]},
                {"finding_id": C, "statement": "Revenue was EUR 21.4bn.",
                 "source_kinds": ["issuer_ir"]},
            ],
            "chair": {"key_points": [{"text": point_text, "finding_id": point_id}],
                      "synthesis": f"Liquidity is strong (finding_id: {MISSING})."},
        }

    def _state(self, v3: dict, pairs: tuple) -> dict:
        tally = _tally(disagreements=len(pairs), unresolved_disagreement_ids=("d1",),
                       disagreement_ids=("d1",), disagreement_pairs=pairs)
        # ADR-095: the pairs a chair may narrate are the run's computed conflicts.
        v3["gap_reconciliation"] = {"conflicts": [
            {"finding_a_id": a, "finding_b_id": b, "disagreement_id": f"d{i + 1}"}
            for i, (a, b) in enumerate(pairs)]}
        return build_canonical_state(v3, tally=tally, as_of=AS_OF)

    def test_a_conflict_no_recorded_disagreement_pairs_is_withheld(self) -> None:
        text = (f"Unresolved disagreement exists on whether the Group reports net cash or "
                f"net debt (finding_id: {A} vs {B}).")
        v3 = self._v3(text, A)
        result = ri.enforce(v3, self._state(v3, ((A, C),)))
        rules = {v.rule for v in result.violations}
        assert ri.RULE_PHANTOM_CONFLICT in rules
        assert ri.RULE_CITED_FINDING_UNKNOWN in rules  # the synthesis id
        assert "Withheld" in result.v3["chair"]["key_points"][0]["text"]

    def test_a_recorded_pair_stands(self) -> None:
        text = f"Findings conflict on net cash (finding_id: {A} vs {B})."
        v3 = self._v3(text, A)
        v3["chair"]["synthesis"] = "Liquidity is strong."
        result = ri.enforce(v3, self._state(v3, ((B, A),)))
        assert [v for v in result.violations
                if v.rule in (ri.RULE_PHANTOM_CONFLICT, ri.RULE_CITED_FINDING_UNKNOWN)] == []
        assert result.v3["chair"]["key_points"][0]["text"] == text

    def test_a_key_point_citing_a_missing_finding_is_withheld(self) -> None:
        v3 = self._v3("Margins expanded.", MISSING)
        result = ri.enforce(v3, self._state(v3, ()))
        assert ri.RULE_CITED_FINDING_UNKNOWN in {v.rule for v in result.violations}

    def test_an_incomplete_payload_list_proves_nothing_absent(self) -> None:
        v3 = self._v3("Margins expanded.", MISSING)
        state = build_canonical_state(v3, tally=_tally(findings=10), as_of=AS_OF)
        assert ri.RULE_CITED_FINDING_UNKNOWN not in {
            v.rule for v in ri.enforce(v3, state).violations}


# ── Review round 1 (NO-GO on 61cb9a2): repros r1–r14 as regressions ────────── #


class TestReviewConflictWording:
    """P1-1 (r9): a conflict word must govern findings; legal disputes and negated forms
    are never a finding conflict."""

    @pytest.mark.parametrize("text", [
        f"The company is in a contract dispute with Acme over $40m of royalties "
        f"(finding_id: {A}, {B}).",
        f"Findings agree, with no conflict: both report net cash of $2.1bn "
        f"(finding_id: {A}, {B}).",
        f"Revenue of $3.2bn is consistent across the 10-K and the release; nothing is "
        f"inconsistent (finding_id: {A}, {B}).",
        f"A pending patent dispute could cut revenue by $200m (finding_id: {A}, {B}).",
        f"Litigation with a former partner is ongoing (finding_id: {A}, {B}).",
        f"There is no disagreement between the findings on cash (finding_id: {A}, {B}).",
        # Re-review R-P1: a negation inside the matched words.
        f"The filings do not conflict: both report net cash of $2.1bn (finding_id: {A}, "
        f"{B}).",
        f"The figures are not inconsistent: revenue is $3.2bn in both (finding_id: {A}, "
        f"{B}).",
        f"The sources don't disagree on cash (finding_id: {A}, {B}).",
        f"The two reports did not contradict each other (finding_id: {A}, {B}).",
        f"Data are consistent; no figures conflict (finding_id: {A}, {B}). Net cash $2.1bn.",
        # Re-review R-P2: descriptive "differ".
        f"The figures differ by region: Americas revenue $3.1bn and EMEA revenue $1.2bn "
        f"(finding_id: {A}, {B}).",
        f"Reports differ in scope: the 10-K covers FY2025 revenue of $3.2bn, the release Q3 "
        f"(finding_id: {A}, {B}).",
    ])
    def test_true_sentences_are_kept(self, text: str) -> None:
        t = TestPhantomConflict()
        v3 = t._v3(text, A)
        v3["chair"]["synthesis"] = "Liquidity is strong."
        result = ri.enforce(v3, t._state(v3, ((A, C),)))
        assert ri.RULE_PHANTOM_CONFLICT not in {v.rule for v in result.violations}
        assert result.v3["chair"]["key_points"][0]["text"] == text

    @pytest.mark.parametrize("text", [
        # Round 3: only explicit research-subject-vs-research-subject forms.
        f"Findings conflict on net cash (finding_id: {A}, {B}).",
        f"The sources contradict each other on cash (finding_id: {A}, {B}).",
        f"The filings disagree on net debt (finding_id: {A}, {B}).",
        f"There is a disagreement between the two sources on cash (finding_id: {A}, {B}).",
        f"There is an inconsistency between the findings (finding_id: {A}, {B}).",
        f"Net cash or net debt (finding_id: {A} vs {B}).",
        f"Finding {A} and finding {B} disagree on net debt: $1.2bn versus $0.8bn.",
        f"(finding_id: {A}) and (finding_id: {B}) disagree on net debt of $1.2bn.",
        f"The filing's net debt of $1.2bn contradicts the release's $0.8bn (finding_id: {A}, "
        f"{B}).",
    ])
    def test_a_conflict_between_findings_is_still_held_to_the_ledger(self, text: str) -> None:
        t = TestPhantomConflict()
        v3 = t._v3(text, A)
        v3["chair"]["synthesis"] = "Liquidity is strong."
        result = ri.enforce(v3, t._state(v3, ((A, C),)))
        assert ri.RULE_PHANTOM_CONFLICT in {v.rule for v in result.violations}

    @pytest.mark.parametrize("text", [
        # r20 — descriptive or non-research uses: never withheld.
        f"Segment figures are different in scope (finding_id: {A}, {B}).",
        f"The company's two plants diverge in output: Mt Weld 10,500t and Kalgoorlie 8,000t "
        f"(finding_id: {A}, {B}).",
        f"Different revenue figures apply to each segment: $3.1bn Americas, $1.2bn EMEA "
        f"(finding_id: {A}, {B}).",
        f"The two filings report different figures because one is FY2025 and the other "
        f"H1 2026 (finding_id: {A}, {B}).",
        f"Statutory and underlying figures differ on a reporting basis: $1.2bn vs $1.4bn "
        f"(finding_id: {A}, {B}).",
        f"Finding {A} reports revenue of $3.2bn and finding {B} contradicts nothing.",
        f"The release's guidance of $1.2bn diverges from consensus of $1.4bn (finding_id: "
        f"{A}, {B}).",
        f"Data diverge from 2024 trends as revenue rose 12% (finding_id: {A}, {B}).",
        f"The company's claim is contested by a regulator; the fine could be $40m "
        f"(finding_id: {A}, {B}).",
        f"Filings disagreeing with management guidance are absent; revenue $3.2bn "
        f"(finding_id: {A}, {B}).",
        f"Sources differ on timing only, both put revenue at $3.2bn (finding_id: {A}, {B}).",
        f"Sources disagree on timing only, both put revenue at $3.2bn (finding_id: {A}, "
        f"{B}).",
        f"Reports differ materially in scope, not in figures (finding_id: {A}, {B}).",
        f"The 10-K figures conflict with nothing in the release (finding_id: {A}, {B}).",
        f"The patent is contested in court; royalties were $40m (finding_id: {A}, {B}).",
        # Accepted misses of the conservative design (ADR-091 round 3): real conflicts
        # worded outside the explicit forms are not withheld.
        f"Conflicting findings exist on net debt (finding_id: {A}, {B}).",
        f"The 10-K figure is at odds with the release (finding_id: {A}, {B}).",
        f"The two sources give different net debt figures, $1.2bn and $0.8bn (finding_id: "
        f"{A}, {B}).",
        f"Net debt is contested: $1.2bn in one filing, $0.8bn in another (finding_id: {A}, "
        f"{B}).",
        f"The two filings diverge on net debt (finding_id: {A}, {B}).",
        f"The figures differ materially on net debt (finding_id: {A}, {B}).",
        f"There is a discrepancy in net debt between the filing and the release, $1.2bn vs "
        f"$0.8bn (finding_id: {A}, {B}).",
        f"The 10-K states net debt of $1.2bn while the release states $0.8bn (finding_id: "
        f"{A}, {B}).",
        # Round 4: "sources of supply" are not research sources.
        f"Conflicts between sources of supply raised costs by $40m (finding_id: {A}, {B}).",
        f"A disagreement between filings of the two subsidiaries delayed the audit "
        f"(finding_id: {A}, {B}).",
    ])
    def test_outside_the_explicit_forms_nothing_is_withheld(self, text: str) -> None:
        t = TestPhantomConflict()
        v3 = t._v3(text, A)
        v3["chair"]["synthesis"] = "Liquidity is strong."
        result = ri.enforce(v3, t._state(v3, ((A, C),)))
        assert ri.RULE_PHANTOM_CONFLICT not in {v.rule for v in result.violations}
        assert result.v3["chair"]["key_points"][0]["text"] == text


class TestReviewStatedSegment:
    """P1-2 (r5): a merely mentioned segment never scopes a Group figure."""

    @pytest.mark.parametrize("source, claim", [
        ("Group revenue rose 7% to $50.1bn in FY2025, driven by strong growth in the "
         "Lending segment.", "Revenue rose 7% to $50.1bn in FY2025."),
        ("Total net revenue was $182.4bn in FY2025; the Asset & Wealth Management segment "
         "grew fastest.", "Net revenue was $182.4bn in FY2025."),
        ("FY2025 revenue of A$1.2bn, excluding the discontinued Retail division.",
         "FY2025 revenue was A$1.2bn."),
        ("Revenue was $3.2bn in FY2025 after the sale of the Oncology business unit.",
         "Revenue was $3.2bn in FY2025."),
    ])
    def test_a_mentioned_segment_scopes_nothing(self, source: str, claim: str) -> None:
        src = cc.source_of_finding({"finding_id": "f1", "statement": source,
                                    "period_key": "FY2025"})
        assert src is not None and src.scope is None
        assert cc.check_claim(claim, [src]) is None

    def test_the_owning_segment_still_scopes(self) -> None:
        src = cc.source_of_finding({"finding_id": "f1", "statement":
                                    "In the Corporate segment, net revenue was $93m in "
                                    "FY2025.", "period_key": "FY2025"})
        assert src is not None and src.scope == "segment:corporate"
        verdict = cc.check_claim("Net revenue was $93m in FY2025.", [src])
        assert verdict is not None and verdict.rule == cc.RULE_CLAIM_SCOPE


class TestReviewPeerOwnership:
    """P1-3 (r11): the subject's own figure is never labelled a peer's."""

    @pytest.mark.parametrize("text", [
        "The company's AISC of A$900/t is below peers' A$1,100/t.",
        "Revenue rose 12% in FY2025, faster than competitors.",
        "Lynas produced 10,500t NdPr in FY2025, ahead of peers.",
        "NdPr output was 10,500t in FY2025 versus rivals' 6,000t.",
        "Our cash cost of $900/t compares with competitors' $1,100/t.",
        # r21 (round 3): no peer-possessive trigger; malformed or detached markers.
        "Unlike peers' A$1,100/t, Lynas's AISC was A$900/t in FY2025.",
        "Peers' average AISC of A$1,100/t compares with Lynas at A$900/t.",
        "Compared with peers' costs, AISC was A$900/t in FY2025.",
        "Ahead of peers' guidance, NdPr output was 10,500t in FY2025.",
        "As the largest producer outside China (a competitor to MP), output was 10,500t.",
        "Lynas (a peer of Iluka) produced 10,500t.",
        "Lynas, not the company's partner, produced 10,500t.",
        "Customers (a rival's buyers) took 10,500t.",
        "Rival's AISC; Lynas produced 10,500t.",
        "Supplying competitors' magnet plants, output reached 10,500t in FY2025.",
        "A peer, not the company: Iluka revenue A$1.2bn.",
        "Peers' average cash cost was $1,100/t in FY2025.",
        # r23 (round 4): comparison / role openers, detached markers, mixed figures.
        "Unlike Iluka (a peer), AISC was A$900/t in FY2025.",
        "Like MP Materials (a competitor), output rose 10% to 10,500t.",
        "Ahead of Iluka (a rival), NdPr output was 10,500t in FY2025.",
        "Unlike Iluka Resources Ltd, AISC was A$900/t in FY2025.",
        "Ahead of MP Materials Corp, NdPr output reached 10,500t in FY2025.",
        "Iluka (a peer) AISC A$1,100/t versus A$900/t at Mt Weld.",
        "Iluka (a peer) reported AISC of A$1,100/t compared with A$900/t at Mt Weld.",
        "Supplier Shin-Etsu Chemical Co (a peer) bought 5,000t from Mt Weld.",
        "Iluka (a peer), AISC was A$1,100/t in FY2025.",
        "Iluka (a peer, not the company): H1 2026 unit cash cost $1,236/t.",
        # Round 4: a named legal entity alone never labels.
        "Rival Mining Ltd reported revenue of A$500m in FY2025.",
        "MP Materials Corp reported NdPr output of 1,300t in Q3 2026.",
        "Neo Performance Materials Inc revenue was US$500m.",
        "Kalgoorlie Rare Earths Processing Pty Ltd processed 12,000t in FY2025.",
        "Competitors such as MP Materials Corp produced 1,300t.",
        # r25 (round 5): lead words, non-verb or relational words after the marker,
        # "while" comparisons, and the subject named anywhere.
        "Outpacing Iluka (a peer) output reached 10,500t in FY2025.",
        "Overtaking MP Materials (a competitor) NdPr output reached 10,500t.",
        "Matching Iluka (a peer) AISC came in at A$900/t.",
        "Alongside Iluka (a peer) the group shipped 10,500t.",
        "Similar To Iluka (a peer) AISC was A$900/t.",
        "Iluka (a peer) bought 5,000t of Lynas concentrate.",
        "Iluka (a peer) supplied 5,000t to Mt Weld.",
        "Iluka (a peer) and Mt Weld together produced 15,000t.",
        "Iluka (a peer) trails at 6,000t while output here was 10,500t.",
        "Iluka (a peer) cut prices and NdPr sales fell 12% to A$500m.",
        "Iluka (a peer) reported 6,000t while output here was 10,500t.",
        "Iluka (a peer) output was 6,000t in FY2025.",
        "Iluka Resources Ltd (a peer, not the company) reported revenue of A$1.2bn; its "
        "margin was 30%.",
        "Arafura Rare Earths Ltd (a peer, not the company) has cash of A$100m; we note its "
        "runway.",
        "Iluka (a peer) reported revenue of A$1.2bn, below Lynas.",
    ])
    def test_the_subjects_figure_is_not_labelled(self, text: str) -> None:
        sec = {"findings": [{"finding_id": "a", "statement": text}]}
        assert pr.label_noncomparable_peer_figures(
            sec, {"name": "Lynas Rare Earths Ltd", "ticker": "LYC"}) == 0
        assert "context_only" not in sec["findings"][0]

    @pytest.mark.parametrize("text", [
        "Iluka (a peer) reported revenue of A$1.2bn in FY2025.",
        "MP Materials (a competitor) produced 1,300t NdPr in Q3 2026.",
        "Iluka (a peer, not the company) reported H1 2026 unit cash cost of $1,236/t.",
        "Iluka Resources (a peer) reported revenue of A$1.2bn in FY2025.",
        "Neo Performance (a rival) sold 5,000t in FY2025.",
        # LIN2's live shape: "up 9% from $1,138/t" is the peer's own prior period.
        "Iluka Resources (a peer, not the company) reported H1 2026 unit cash cost of "
        "$1,236/t of Z/R/SR produced, up 9% from $1,138/t.",
    ])
    def test_another_owner_is_labelled(self, text: str) -> None:
        sec = {"findings": [{"finding_id": "a", "statement": text}]}
        assert pr.label_noncomparable_peer_figures(
            sec, {"name": "Lynas Rare Earths Ltd", "ticker": "LYC"}) == 1

    @pytest.mark.parametrize("text, labelled", [
        # Round 5: the subject named anywhere (even a later "its" / "we") blocks; a
        # possessive never labels.
        ("Iluka Resources Ltd (a peer, not the company) reported revenue of A$1.2bn; its "
         "margin was 30%.", False),
        ("Arafura Rare Earths Ltd (a peer, not the company) has cash of A$100m; we note its "
         "runway.", False),
        ("MP Materials (a competitor) produced 1,300t NdPr in Q3 2026.", True),
        ("Peers' AISC averaged A$1,100/t in FY2025; the company's was lower.", False),
        # A subject pronoun or name BEFORE the first figure in its clause still blocks.
        ("Its peer Iluka (a peer, not the company) reported revenue of A$1.2bn.", False),
        ("Lynas Kalgoorlie Pty Ltd processed 12,000t of concentrate.", False),
        ("Lynas Malaysia Sdn Bhd produced 10,500t NdPr in FY2025.", False),
        # A legal name alone, with the subject possibly named later: kept (missing is fine).
        ("Iluka Resources Ltd revenue was A$1.2bn in FY2025; the company expects growth.",
         False),
        ("MP Materials Corp reported NdPr output of 1,300t in Q3 2026, above its guidance.",
         False),
        ("In FY2025 the Mt Weld mine produced 10,500t; MP Materials Corp produced 1,300t.",
         False),
    ])
    def test_the_first_figures_owner_decides(self, text: str, labelled: bool) -> None:
        sec = {"findings": [{"finding_id": "a", "statement": text}]}
        assert pr.label_noncomparable_peer_figures(
            sec, {"name": "Lynas Rare Earths Ltd", "ticker": "LYC"}) == int(labelled)

    def test_the_subjects_own_entity_by_another_spelling_is_not_labelled(self) -> None:
        sec = {"findings": [{"finding_id": "a", "statement":
                             "J.P. Morgan SE held EUR 3.2tn of client assets in 2024."}]}
        assert pr.label_noncomparable_peer_figures(
            sec, {"name": "JPMorgan Chase & Co", "ticker": "JPM"}) == 0


class TestReviewSubLine:
    """P1-4 (r6, r7): legal suffixes, whole-company words, revenue family only, and the
    requirement's own clause."""

    @pytest.mark.parametrize("text, name, ticker", [
        ("Cellectis S.A. revenue was $50m in FY2025.", "Cellectis S.A.", "CLLS"),
        ("Cellectis SA revenue was $50m in FY2025.", "Cellectis S.A.", "CLLS"),
        ("Lynas Rare Earths Ltd revenue was A$500m.", "Lynas Rare Earths Ltd", "LYC"),
        ("Worldwide revenue was $3.2bn", "Moderna, Inc.", "MRNA"),
        ("Global revenue was $3.2bn", "Moderna, Inc.", "MRNA"),
        ("Company-wide revenue was $50m", "Moderna, Inc.", "MRNA"),
    ])
    def test_the_whole_company_is_not_a_product_line(self, text, name, ticker) -> None:  # noqa: ANN001
        words = gr.subject_words(name, ticker)
        assert gr._sub_line_reasons(gr.FindingFacts("f", text, ()), words, None) == []

    def test_a_product_line_still_differs(self) -> None:
        words = gr.subject_words("Moderna, Inc.", "MRNA")
        facts = gr.FindingFacts("f", "Spikevax revenue was $1.9bn in FY2025.", ())
        assert gr._sub_line_reasons(facts, words, None) == [gr.REASON_SCOPE_DIFFERS]

    @pytest.mark.parametrize("text", [
        "Cash and cash equivalents were $7.5bn at 30 September 2026; Product revenue was "
        "$1.0bn in Q3 2026.",
        "Cash and cash equivalents were $7.5bn at 30 September 2026; Worldwide revenue was "
        "$1.0bn in Q3 2026.",
    ])
    def test_a_cash_gap_is_not_judged_by_a_revenue_clause(self, text: str) -> None:
        gap = _gap("Cash and cash equivalents at the latest balance sheet date not "
                   "established")
        verdict = _one(gap, [_finding(A, text)], subject_name="Moderna, Inc.",
                       subject_ticker="MRNA")
        assert gr.REASON_SCOPE_DIFFERS not in verdict.reasons
        assert verdict.gap_status != gr.GAP_STATUS_NONCOMPARABLE

    def test_worldwide_revenue_closes_a_revenue_gap(self) -> None:
        verdict = _one(_gap("FY2025 revenue not established"),
                       [_finding(A, "Worldwide revenue was $3.2bn in FY2025.")],
                       subject_name="Moderna, Inc.", subject_ticker="MRNA")
        assert verdict.gap_status == gr.GAP_STATUS_CLOSED

    def test_only_the_requirement_clause_is_read(self) -> None:
        words = gr.subject_words("Moderna, Inc.", "MRNA")
        facts = gr.FindingFacts(
            "f", "Revenue was $3.2bn in FY2025; Spikevax revenue was $1.9bn.", ())
        assert gr._sub_line_reasons(facts, words, None, "metric:revenue") == []
        assert gr._sub_line_reasons(facts, words, None, "metric:cash") == []


class TestReviewFiscalCalendar:
    """P2-1 / P2-2 (r1–r4) and P3: half-years are not year ends; interim labels and the
    rekey follow the fiscal calendar."""

    @staticmethod
    def _f(i: int, text: str, key: str | None = None, pub: str | None = None) -> dict:
        return {"finding_id": f"f{i}", "statement": text, "source_kinds": ["issuer_filing"],
                "period_key": key, "source_published_at": pub}

    def test_a_half_year_end_is_no_year_end(self) -> None:
        v3 = {"findings": [
            self._f(1, "Revenue for the half-year ended 30 September 2025 was INR 100bn."),
            self._f(2, "Net profit for the half year ended 30 September 2025 rose 10%."),
            self._f(3, "EBITDA for the six months ended 30 September 2025 was INR 20bn."),
            self._f(4, "Revenue for the nine months ended 31 December 2025 rose."),
            self._f(5, "Revenue for the year ended 31 March 2025 was INR 180bn."),
        ]}
        assert cs.fiscal_year_end(v3) is None  # one real year-end mention only
        v3b = {"findings": [
            self._f(1, "Revenue for the half-year ended 31 December 2025 was A$10m."),
            self._f(2, "Loss for the half-year ended 31 December 2025 was A$3m."),
            self._f(3, "the year ended 30 June 2025 revenue was A$18m"),
        ]}
        assert cs.fiscal_year_end(v3b) is None

    def test_half_year_mentions_do_not_unseat_the_latest_period(self) -> None:
        base = [
            self._f(9, "Profit for the year ended 31 March 2026 rose.", "FY2026", "2026-06-20"),
            self._f(1, "Revenue for the year ended 31 March 2026 was INR 180bn.", "FY2026",
                    "2026-06-20"),
            dict(self._f(5, "Q1 FY2027 revenue INR 50bn", "2027-Q1", "2026-08-10"),
                 period_basis="text_stated"),
        ]
        half = [self._f(i, "EBITDA for the half-year ended 30 September 2025 was INR 20bn.",
                        "2026-H1", "2025-11-10") for i in (2, 3, 4, 6, 7, 8)]
        got = cs._latest_period({"findings": base + half}, date(2026, 11, 8))
        assert got[:2] == ("2027-Q1", date(2026, 6, 30))
        assert cs.fiscal_year_end({"findings": base + half})["month"] == 3

    @staticmethod
    def _interim(fye: str, findings: list[dict]) -> str | None:
        v3 = {"core_filings": {"annual": {"state": "ready", "period_end": fye}},
              "findings": findings, "financial_statements_state": {"reporting_periods": {}}}
        got = cs._latest_interim(v3, {"covered_by_issuer_findings": True}, date(2026, 11, 8))
        return None if got is None else got["label"].split(" — ")[0]

    def test_latest_interim_is_read_and_labelled_on_the_fiscal_calendar(self) -> None:
        fiscal = [dict(self._f(1, "Q1 FY2027 revenue $5m", "2027-Q1"),
                       period_basis="text_stated"),
                  dict(self._f(2, "Q3 FY2026 revenue $4m", "2026-Q3"),
                       period_basis="text_stated")]
        assert self._interim("2026-03-31", fiscal) == "Q1 FY2027"

    def test_an_inherited_key_is_ambiguous_for_a_non_december_year_end(self) -> None:
        """Re-review R-P7 / round 3: an inherited key is neither read on the fiscal
        calendar nor shown as a calendar quarter — it is skipped (missing beats wrong)."""
        tagged = [self._f(1, "Revenue rose 8% in the quarter.", "2026-Q3")]
        assert self._interim("2026-03-31", tagged) is None
        # No period_basis and a key that is in the future on the calendar: not held.
        assert self._interim("2026-03-31", [self._f(2, "Revenue $5m", "2027-Q1")]) is None

    def test_a_september_year_end_sec_filer_interim(self) -> None:
        # Fiscal Q4 FY2026 = Jul–Sep 2026, ended; fiscal Q1 FY2027 = Oct–Dec 2026, not yet.
        stated = [dict(self._f(1, "Q4 FY2026 revenue was $90bn", "2026-Q4"),
                       period_basis="text_stated"),
                  dict(self._f(2, "Q1 FY2027 guidance", "2027-Q1"),
                       period_basis="text_stated")]
        assert self._interim("2026-09-27", stated) == "Q4 FY2026"
        # The same filer's inherited tag is ambiguous: skipped.
        assert self._interim("2026-09-27", [self._f(3, "Revenue $90bn", "2026-Q3")]) is None
        # A December filer never takes a fiscal label.
        assert self._interim("2025-12-31", [dict(self._f(4, "Q3 revenue", "2026-Q3"),
                                                 period_basis="text_stated")]) == "Q3 2026"

    @staticmethod
    def _sec(fye: str, findings: list[dict], **extra) -> dict:  # noqa: ANN003
        return {"core_filings": {"annual": {"state": "ready", "period_end": fye}},
                "findings": findings, **extra}

    def test_a_forward_tag_is_not_the_latest_period(self) -> None:
        """r22 — "runway into Q1 2027" tagged 2027-Q1 on a March year end is not the
        latest reported period (it would read as the fiscal Q1, ended June 2026)."""
        annual = self._f(1, "Revenue for the year ended 31 March 2026 was $1.8bn.", "FY2026",
                         "2026-06-20")
        runway = self._f(2, "Cash runway extends into Q1 2027.", "2027-Q1", "2026-08-10")
        got = cs._latest_period(self._sec("2026-03-31", [annual, runway]), date(2026, 11, 8))
        assert got[0] != "2027-Q1"
        calendar_q4 = self._f(3, "Q4 2026 revenue is expected to rise.", "2026-Q4",
                              "2026-08-10")
        got = cs._latest_period(self._sec("2026-03-31", [annual, calendar_q4]),
                                date(2026, 11, 8))
        assert got[0] != "2026-Q4"
        # The inherited annual key is ambiguous too: nothing rather than a guess.
        assert got == (None, None, None)

    def test_a_known_fiscal_key_still_counts(self) -> None:
        stated = dict(self._f(1, "Q1 FY2027 revenue was $0.5bn.", "2027-Q1", "2026-08-10"),
                      period_basis="text_stated")
        got = cs._latest_period(self._sec("2026-03-31", [stated]), date(2026, 11, 8))
        assert got == ("2027-Q1", date(2026, 6, 30), "issuer_finding")
        facts = self._sec("2026-03-31", [], financial_statements_state={
            "reporting_periods": {"latest_current_period": "Q1 2027"}})
        assert cs._latest_period(facts, date(2026, 11, 8))[:2] == ("Q1 2027",
                                                                  date(2026, 6, 30))

    def test_a_calendar_year_issuer_is_unchanged(self) -> None:
        tag = self._f(1, "Revenue for Q3 2026 rose.", "2026-Q3", "2026-10-30")
        got = cs._latest_period(self._sec("2025-12-31", [tag]), date(2026, 11, 8))
        assert got == ("2026-Q3", date(2026, 9, 30), "issuer_finding")
        got = cs._latest_period({"findings": [tag]}, date(2026, 11, 8))
        assert got == ("2026-Q3", date(2026, 9, 30), "issuer_finding")

    def test_a_september_year_end_sec_filer_latest_period(self) -> None:
        inherited = self._f(1, "Revenue rose.", "2026-Q3", "2026-10-30")
        assert cs._latest_period(self._sec("2026-09-27", [inherited]),
                                 date(2026, 11, 8)) == (None, None, None)
        stated = dict(self._f(2, "Q4 FY2026 revenue was $90bn.", "2026-Q4", "2026-10-30"),
                      period_basis="text_stated")
        assert cs._latest_period(self._sec("2026-09-27", [stated]), date(2026, 11, 8)) == (
            "2026-Q4", date(2026, 9, 30), "issuer_finding")

    def test_a_september_year_end_sec_filer(self) -> None:
        # A 52/53-week year ending on 27 September is a September year end.
        v3 = {"core_filings": {"annual": {"state": "ready", "period_end": "2026-09-27"}}}
        assert cs.fiscal_year_end(v3) == {"month": 9, "basis": "sec_annual_period_of_report"}
        assert cs.period_bounds("2027-Q1", fiscal_year_end_month=9) == (
            date(2026, 10, 1), date(2026, 12, 31))
        assert cs.period_bounds("FY2026", fiscal_year_end_month=9) == (
            date(2025, 10, 1), date(2026, 9, 30))

    @pytest.mark.parametrize("text", [
        "Revenue for the first quarter ended March 31, 2025 was $10m.",
        "Q1 2026 revenue was $12m, up 20% from $10m in the first quarter ended March 31, "
        "2025.",
        "Revenue of $12m compared with $10m in Q1 2025.",
        "In Q1 2025 the company sold 10 units.",
        "Revenue was $12m for the quarter, against $10m for the first quarter of 2025.",
        "First quarter 2025 comparatives restated: revenue $10m.",
    ])
    def test_a_comparison_quarter_never_rekeys(self, text: str) -> None:
        assert inv._fiscal_quarter_restated("2026-Q1", text, date(2026, 5, 5),
                                            date(2026, 11, 8)) is None

    @pytest.mark.parametrize("inherited, text, expected", [
        ("2026-Q1", "Revenue for the first quarter ended 30 June 2026 was $12m.", "2027-Q1"),
        ("2026-Q1", "Q1 FY2027 revenue was $12m.", "2027-Q1"),
        ("2026-Q2", "Revenue for the second quarter ended 30 June 2026 was $12m.", None),
        ("2026-Q2", "Revenue for the quarter ended 30 June 2026 was $12m.", None),
        # A key already on the fiscal year never loses to a printed calendar label.
        ("2027-Q1", "Q1 2026 revenue was $12m.", None),
        ("2027-Q1", "Q1 FY26 revenue was $12m.", None),
    ])
    def test_rekey_only_calendar_to_fiscal(self, inherited, text, expected) -> None:  # noqa: ANN001
        assert inv._fiscal_quarter_restated(inherited, text, date(2026, 8, 1),
                                            date(2026, 11, 8)) == expected


class TestReviewCommercialClaims:
    """P2-4 (r12)."""

    SUB = {"name": "Arafura Rare Earths Ltd", "ticker": "ARU"}

    @pytest.mark.parametrize("text", [
        "Arafura makes steady progress on Nolans financing.",
        "The company processes no ore yet; Nolans remains in development.",
        "Arafura extracts value from its offtake agreements.",
        "Arafura has a pilot plant producing test batches of NdPr oxide.",
        "Arafura has finalised the design for the Nolans plant producing 4,440tpa NdPr "
        "once commissioned.",
        "The company ships samples to potential customers.",
        "Arafura makes a final investment decision in 2027.",
        "The company makes payments to contractors.",
        "Arafura operates a demonstration plant producing NdPr oxide.",
        "The company owns the Nolans plant producing NdPr when complete.",
    ])
    def test_not_a_commercial_operation(self, text: str) -> None:
        assert not ccl.claims_commercial_operation(text, self.SUB)

    @pytest.mark.parametrize("text", [
        "Arafura processes 1.2Mtpa of ore at Nolans.",
        "The company owns the Nolans plant producing NdPr oxide.",
        "Arafura ships concentrate to Japan.",
    ])
    def test_still_a_claim(self, text: str) -> None:
        assert ccl.claims_commercial_operation(text, self.SUB)


class TestReviewContrastDenial:
    """P3 (r8): only the contrasted tail is denied."""

    @pytest.mark.parametrize("deny, stated, pairs", [
        ("The H1 2026 report discloses cash, but not net debt.",
         "Net debt was $120m at 30 June 2026.", 1),
        ("The H1 2026 report gives revenue, not EBITDA.", "H1 2026 EBITDA was $40m.", 0),
        ("The H1 2026 report gives revenue, not EBITDA.", "H1 2026 revenue was $400m.", 0),
        ("The H1 2026 report states revenue rather than EBITDA.",
         "H1 2026 EBITDA was $40m.", 0),
        ("The H1 2026 report states revenue rather than EBITDA.",
         "H1 2026 revenue was $400m.", 0),
        # HON2 replay: a head stating a figure only describes that figure's period.
        ("Group revenue for 2026-Q3 was USD 4,994m, a single-quarter figure for the quarter "
         "ended 2026-09-30 (10-Q filed 2026-10-22) — quarterly, not annual.",
         "Group revenue was USD 37,442m for FY2025 (annual period ended 2025-12-31), per "
         "the 10-K filed 2026-02-17.", 0),
        ("Revenue is reported annually, not quarterly.",
         "Q2 2026 net sales were $4,289.0 million.", 1),
    ])
    def test_contrast(self, deny: str, stated: str, pairs: int) -> None:
        assert len(gr.contradicted_absences([_finding(A, deny), _finding(B, stated)])) == pairs


class TestReviewCoveringFacts:
    """P3: stale covering facts, and the comparable partial answer preferred."""

    def test_newest_annual_year_and_stale(self) -> None:
        facts = [gr.FactFacts("a", "capex", "metric:capex", period="FY2023"),
                 gr.FactFacts("b", "revenue", "metric:revenue", period="FY2025"),
                 gr.FactFacts("c", "revenue", "metric:revenue", period="2026-Q2")]
        newest = gr._newest_annual_year(facts)
        assert newest == 2025
        assert gr._fact_is_stale(facts[0], newest)
        assert not gr._fact_is_stale(facts[1], newest)
        assert not gr._fact_is_stale(facts[0], None)
        assert gr._newest_annual_year([]) is None

    def test_a_comparable_partial_answer_is_preferred(self) -> None:
        other_scope = _finding("f1", "Wealth revenue was US$5bn", period_key="FY2025",
                               scope_key="segment:wealth")
        # ADR-094: an older period is no longer comparable; a third-party figure for the
        # same period, metric and scope is.
        third_party = _finding("f2", "Revenue was US$40m for FY2025.", period_key="FY2025",
                               source_kinds=("secondary_web",))
        verdict = _one(_gap("FY2025 revenue was not acquired"), [other_scope, third_party])
        assert verdict.gap_status == gr.GAP_STATUS_PARTIALLY_CLOSED
        assert verdict.finding_ids[:1] == ["f2"]


class TestReviewRobustness:
    """r14: malformed payloads never raise."""

    @pytest.mark.parametrize("v3", [
        {}, {"primary_document_coverage": {"documents": None}},
        {"primary_document_coverage": {"documents": [None, "x",
                                                     {"extraction_status": "extracted"}]}},
        {"financial_statements_state": {"slots": None, "reporting_periods": None}},
        {"core_filings": {"annual": {"state": "ready", "period_end": "garbage"}}},
        {"consumption": {"web_search_calls": "n/a", "web_stage": None},
         "loop": {"gaps_open": "x"}, "gaps": None,
         "findings": [None, {"statement": None, "source_kinds": None}]},
    ])
    def test_malformed(self, v3: dict) -> None:
        state = build_canonical_state(v3, as_of=date(2026, 11, 8))
        ri.enforce(v3, state)
        ri.enforce_content({"evidence_channels": {"channels": [None, {"detail":
                                                                      "not sourced"}]},
                            "data_availability_summary": None}, state)
        assert isinstance(state["status"]["documents_read"], int)

    def test_a_state_without_status(self) -> None:
        out, _ = ri.enforce_content({"evidence_channels": {"channels": [
            {"channel": "issuer_newsroom", "detail": "not sourced"}]}}, {"gaps": {"open": 2}})
        assert out is not None


def test_review_patterns_are_linear_on_their_own_worst_cases() -> None:
    """r10 / r13 — the reviewer's worst cases."""
    cases = [
        (ccl._ASSET_PARTICIPLE_RE, "plant " + "a," * 2000 + " x" * 2000),
        (ccl._ASSET_PARTICIPLE_RE, ("plant " + "word " * 3) * 800),
        (cc._STATED_SEGMENT_RE, ("Aa " * 4 + "& ") * 1000),
        (cc._STATED_SEGMENT_RE, "A" * 31 * 200 + " "),
        (cs._YEAR_END_STATED_RE, "year ended " * 2000),
        (gr._CONTRAST_NOT_ABSENCE, ", " * 3000),
        (ri._CONFLICT_WORDS_RE, "findings " * 3000),
        (ri._CONFLICT_WORDS_RE, "conflict " * 3000),
        (ri._CONFLICT_WORDS_RE, "filing's " * 3000),
        (ri._CONFLICT_WORDS_RE, (A + ") ") * 300),
        (ri._CONFLICT_WORDS_RE, "findings $1.2bn, " * 1500),
        (ri._CONFLICT_WORDS_RE, "different " * 3000),
        (cc._MENTIONED_SEGMENT_RE, "excluding " + "word " * 3000),
    ]
    for pattern, text in cases:
        started = time.perf_counter()
        list(pattern.finditer(text))
        assert time.perf_counter() - started < 0.2
    started = time.perf_counter()
    ccl.claims_commercial_operation("It owns " + "the plant " * 1000 + " making",
                                    {"name": "Lynas"})
    cc.stated_segment("$1" * 5000)
    cc.stated_segment("In the Corporate segment, revenue was $1m; " * 200)
    assert time.perf_counter() - started < 1.0


# ── Linear-time patterns ───────────────────────────────────────────────────── #


PATTERNS = [
    gr._LINE_METRIC_RE, gr._CONTRAST_NOT_ABSENCE, gr._SOURCE_LABEL_NEGATION,
    ccl._CLAIM_RE, ccl._ASSET_PARTICIPLE_RE, cc._STATED_SEGMENT_RE,
    ri._BLOCKING_GAPS_RE, ri._CONFLICT_WORDS_RE, ri._CONFLICT_NEGATION_RE, pr._FIGURE_RE,
    pr._NOT_THE_COMPANY_RE, pr._MARKED_OWNER_RE, pr._SUBJECT_PRONOUN_RE,
    pr._DETACHED_RE, pr._COMPARISON_AFTER_RE, pr._FIRST_WORD_RE,
    cc._WHOLE_COMPANY_RE, cc._MENTIONED_SEGMENT_RE, ccl._FORWARD_RE,
    inv._BARE_OR_FY_YEAR_RE, inv._QUARTER_KEY_RE, cs._YEAR_END_STATED_RE,
    cs._PART_YEAR_BEFORE_RE, cc._SEGMENT_CLAUSE_RE, gr._HEAD_FIGURE_RE,
    ccl._NON_PRODUCT_OBJECT_RE, ccl._POST_VERB_NEGATION_RE, ccl._FORWARD_TAIL_RE,
    ccl._PILOT_ASSET_RE, ri._NEGATION_IN_SPAN_RE, ri._NEGATION_AFTER_RE,
    # ADR-093: segment hierarchy, period duration, hierarchy claim checks.
    sh._LIST_CUE_RE, sh._LIST_LEAD_RE, sh._LIST_END_RE, sh._ACRONYM_RE, sh._NAME_WORD_RE,
    sh._HEADING_CHARS_RE, sh._LINE_REVENUE_RE, sh._FIRM_WORDS_RE, sh._MENTION_BEFORE_RE,
    sh._PAST_STRUCTURE_RE, sh._FIGURE_RE, pdur._YTD_WORDS_RE, pdur._QUARTER_WORDS_RE,
    pdur._MARKER_LINE_RE, pdur._HEADER_DURATION_RE, pdur._CELL_RE, pdur._SENTENCE_END_RE,
    cp._KEY_YTD_RE, cp._YTD_LABEL_RE, cc._DRIVER_BEFORE_RE, cc._DRIVER_AFTER_RE,
    cc._AS_SEGMENT_AFTER_RE, cc._REVENUE_WORDS_RE, cc._METRIC_WORD_RE,
    sh._KEY_SEP_RE, sh._CAMEL_RE, sh._TITLE_HEADING_RE, sh._CLAUSE_RE, sh._FIRMWIDE_ITEM_RE,
    sh._LEADING_JOINER_RE, sh._BLANKS_RE, sh._ITEM_SPLIT_RE, sh._OXFORD_RE, sh._AND_RE,
    pdur._QUARTER_KEY_RE, pdur._STATEMENT_YTD_RE, pdur._INSTANT_RE, cc._WS_RE,
    cc._LABEL_LEAD_RE, cc._TAIL_PUNCT_RE, pdur._FORWARD_BEFORE_RE, pdur._YEAR_RE,
    pdur._SHORT_YEAR_RE, cc._LABEL_PERIOD_RE,
    # ADR-095: deterministic conflicts.
    gr._YTD_WORDS_RE, gr._QUARTER_WORDS_RE, gr._ISO_DATE_RE, gr._METRIC_LINK_RE,
    ri._CONFLICT_COUNT_RE,
]
DEGENERATE = [" " * 10000, "\n" * 10000, "A-" * 3000, "a," * 5000, "a." * 3000]


@pytest.mark.parametrize("pattern", PATTERNS, ids=lambda p: p.pattern[:30])
def test_new_patterns_are_linear(pattern) -> None:  # noqa: ANN001
    # ADR-093: thousands of match objects can trigger a collector pass over the whole
    # suite's heap; that is not the pattern's time.
    was_enabled = gc.isenabled()  # re-review R4: restore, never force on
    gc.disable()
    try:
        for text in DEGENERATE:
            start = time.perf_counter()
            for _m in pattern.finditer(text):
                pass
            assert time.perf_counter() - start < 0.2
    finally:
        if was_enabled:
            gc.enable()


def test_new_helpers_are_fast_on_degenerate_text() -> None:
    words = gr.subject_words("Example Minerals Ltd", "EXM")
    for text in DEGENERATE:
        facts = gr.FindingFacts(finding_id=A, statement=text, fields=())
        for call in (
            lambda: cc.stated_segment(text),
            lambda: ccl.claims_commercial_operation(text, SUBJECT),
            lambda: gr._sub_line_reasons(facts, words, None),  # noqa: B023
            lambda: gr._contrasts_two_fields(text[:600]),
            lambda: inv._fiscal_quarter_restated("2026-Q1", text, None, AS_OF),
            lambda: cs.fiscal_year_end({"findings": [
                {"finding_id": A, "statement": text, "source_kinds": ["issuer_ir"]}]}),
        ):
            start = time.perf_counter()
            call()
            assert time.perf_counter() - start < 0.5
