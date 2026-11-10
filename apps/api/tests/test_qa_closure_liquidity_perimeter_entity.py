"""ADR-096 — liquidity components, a change of perimeter, the implicit-subject firewall.

Research Integrity Closure H6 (spec §11–§13). Fixtures follow the round-3 evaluation
shapes: a biotech's runway that left out USD 144.8m of term deposits; an industrial's
pre-spin-off debt and backlog presented as current; current-development documents and
findings about other companies under four subjects. No network.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.calculations import liquidity as liq
from app.services.entities import subject_mention as sm
from app.services.ledger import perimeter as pm

NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)
URL = "https://www.sec.gov/Archives/edgar/data/1627281/000119312526116428/clls-20251231.htm"


def _fact(field: str, value: float, period: str = "2025", scale: str = "thousand",
          currency: str = "USD", *, concept: str | None = None, text: str | None = None,
          ) -> dict[str, Any]:
    return {
        "field": field,
        "value": text or f"{value:,.0f}",
        "numeric_value": value,
        "unit": "currency_amount",
        "currency": currency,
        "scale": scale,
        "period": period,
        "scope": "Group",
        "source_url": URL,
        "excerpt_id": f"ixbrl:{concept or 'ifrs-full:X'}@c1",
        "confidence": "high",
        "fact_id": str(uuid.uuid4()),
        "document_id": "doc-20f",
        "document_title": "20-F 0001193125-26-116428",
        "document_date": "2026-04-15",
        "document_source_type": "sec_filing",
        "document_source_tier": "T1_primary_filing",
        "document_period_end": None,
    }


def _biotech(*extra: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _fact("cash_and_equivalents", 61533.0, concept="ifrs-full:CashAndCashEquivalents"),
        _fact("operating_cash_flow", -39401.0,
              concept="ifrs-full:CashFlowsFromUsedInOperatingActivities", text="(39,401)"),
        _fact("capital_expenditure", 2360.0, concept=(
            "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities")),
        *extra,
    ]


def _state(facts: list[dict[str, Any]]) -> dict[str, Any]:
    from app.services.pipeline.issuer_financials import build_financial_statements_state

    return build_financial_statements_state(
        facts, now=NOW, subject_name="Vexgene SA", subject_ticker="VXG")


# ── §11 liquidity ─────────────────────────────────────────────────────────── #


class TestLiquidityModel:
    def test_the_concept_map_reads_each_component_into_its_own_field(self) -> None:
        from app.integrations.xbrl_statement_concepts import STATEMENT_CONCEPT_BY_QNAME as m

        assert m["ifrs-full:ShorttermDepositsNotClassifiedAsCashEquivalents"].statement_field \
            == "short_term_deposits"
        assert m["ifrs-full:CurrentRestrictedCashAndCashEquivalents"].statement_field \
            == "restricted_cash"
        assert m["ifrs-full:CurrentFinancialAssets"].statement_field \
            == "current_financial_assets"
        assert m["us-gaap:ShortTermInvestments"].statement_field == "short_term_investments"
        assert m["us-gaap:MarketableSecuritiesCurrent"].statement_field \
            == "short_term_investments"
        assert m["us-gaap:RestrictedCashCurrent"].statement_field == "restricted_cash"
        # None of them ever fills cash and cash equivalents.
        assert all(c.statement_field != "cash_and_equivalents" for q, c in m.items()
                   if "Deposit" in q or "Restricted" in q or "Investments" in q)

    def test_available_liquidity_sums_cash_and_same_date_deposits_restricted_excluded(
        self,
    ) -> None:
        slots = {
            "cash_and_equivalents": _fact("cash_and_equivalents", 61533.0),
            "short_term_deposits": _fact("short_term_deposits", 144.8, scale="million",
                                         text="144.8"),
            "restricted_cash": _fact("restricted_cash", 2.0, scale="million", text="2.0"),
            "current_financial_assets": _fact("current_financial_assets", 147130.0),
        }
        view = liq.build_liquidity(slots)
        assert view["state"] == liq.STATE_COMPLETE
        assert view["available_liquidity"]["numeric_value"] == pytest.approx(206333.0)
        assert view["available_liquidity"]["scale"] == "thousand"
        roles = {c["component"]: c["role"] for c in view["components"]}
        assert roles == {
            "cash_and_equivalents": liq.ROLE_ANCHOR,
            "short_term_deposits": liq.ROLE_INCLUDED,
            "restricted_cash": liq.ROLE_EXCLUDED,
            "current_financial_assets": liq.ROLE_AGGREGATE,
        }
        # Every included component is cited: value, date, document, concept.
        deposit = next(c for c in view["components"] if c["component"] == "short_term_deposits")
        assert deposit["fact_id"] and deposit["document_title"] and deposit["period"] == "2025"
        assert liq.basis_label(view) == "cash and cash equivalents + short-term deposits"

    def test_a_component_held_for_another_date_makes_it_cash_only_never_silently(self) -> None:
        slots = {
            "cash_and_equivalents": _fact("cash_and_equivalents", 61533.0),
            "short_term_deposits": _fact("short_term_deposits", 115.8, period="2024",
                                         scale="million", text="115.8"),
        }
        view = liq.build_liquidity(slots)
        assert view["state"] == liq.STATE_CASH_ONLY_INCOMPLETE
        assert view["available_liquidity"] is None
        assert view["excludes"] == ["short-term deposits"]
        assert liq.basis_label(view) == "cash only, excludes short-term deposits"
        # Known to exist only as a held fact of another period (no slot at all).
        held = liq.build_liquidity({"cash_and_equivalents": slots["cash_and_equivalents"]},
                                   held_fields={"short_term_investments"})
        assert held["excludes"] == ["short-term investments"]

    def test_an_unexplained_current_financial_assets_aggregate_is_a_missing_component(
        self,
    ) -> None:
        view = liq.build_liquidity({
            "cash_and_equivalents": _fact("cash_and_equivalents", 61533.0),
            "current_financial_assets": _fact("current_financial_assets", 147130.0),
        })
        assert view["state"] == liq.STATE_CASH_ONLY_INCOMPLETE
        assert view["excludes"] == ["current financial assets"]

    def test_another_currency_or_scope_is_not_the_same_basis(self) -> None:
        other = _fact("short_term_deposits", 100.0, scale="million", currency="EUR")
        view = liq.build_liquidity({
            "cash_and_equivalents": _fact("cash_and_equivalents", 61533.0),
            "short_term_deposits": other,
        })
        assert view["state"] == liq.STATE_CASH_ONLY_INCOMPLETE
        segment = dict(_fact("short_term_deposits", 100.0, scale="million"),
                       scope="Segment: Plants")
        assert liq.build_liquidity({
            "cash_and_equivalents": _fact("cash_and_equivalents", 61533.0),
            "short_term_deposits": segment,
        })["state"] == liq.STATE_CASH_ONLY_INCOMPLETE

    def test_no_cash_no_liquidity(self) -> None:
        assert liq.build_liquidity({})["state"] == liq.STATE_NO_CASH


class TestRunwayExposesItsComponents:
    def test_the_runway_lasts_on_cash_plus_term_deposits(self) -> None:
        state = _state(_biotech(_fact("short_term_deposits", 144.8, scale="million",
                                      text="144.8")))
        (runway,) = state["derived"]
        assert runway["status"] == "computed"
        # (61,533 + 144,800) / ((39,401 + 2,360) / 4) — the cash-only reading was 5.9.
        assert runway["value"] == pytest.approx(19.76, abs=0.01)
        assert runway["liquidity_state"] == liq.STATE_COMPLETE
        assert runway["liquidity_components_used"] == [
            "cash_and_equivalents", "short_term_deposits"]
        assert "short-term deposits" in runway["label"]
        assert runway["inputs"]["cash"]["label"] == "available_liquidity"
        assert state["liquidity"]["annual"]["state"] == liq.STATE_COMPLETE
        assert state["slots"]["short_term_deposits_primary_filing"]["value"] == "144.8"

    def test_a_missing_component_labels_the_runway_cash_only(self) -> None:
        state = _state(_biotech(_fact("short_term_deposits", 115.8, period="2024",
                                      scale="million", text="115.8")))
        (runway,) = state["derived"]
        assert runway["value"] == pytest.approx(5.89, abs=0.01)
        assert runway["label"].startswith("Cash runway (cash only, excludes short-term deposits")
        assert runway["liquidity_state"] == liq.STATE_CASH_ONLY_INCOMPLETE
        assert runway["liquidity"]["excludes"] == ["short-term deposits"]

    def test_cash_alone_with_nothing_else_known_keeps_the_plain_label(self) -> None:
        (runway,) = _state(_biotech())["derived"]
        assert runway["label"] == "Cash runway (quarter-equivalents, derived)"
        assert runway["liquidity_state"] == liq.STATE_COMPLETE
        assert runway["liquidity_components_used"] == ["cash_and_equivalents"]

    def test_restricted_cash_never_lengthens_the_runway(self) -> None:
        (runway,) = _state(_biotech(_fact("restricted_cash", 2048.0)))["derived"]
        assert runway["value"] == pytest.approx(5.89, abs=0.01)


# ── §12 perimeter ─────────────────────────────────────────────────────────── #


SPIN = "a0000000-0000-0000-0000-000000000001"
DEBT = "b0000000-0000-0000-0000-000000000002"
BACKLOG_OLD = "c0000000-0000-0000-0000-000000000003"
BACKLOG_Q2 = "d0000000-0000-0000-0000-000000000004"
BACKLOG_Q3 = "e0000000-0000-0000-0000-000000000005"
INDUSTRY = "f0000000-0000-0000-0000-000000000006"


def _finding(fid: str, statement: str, *, period_key: str | None = None,
             kinds: tuple[str, ...] = ("issuer_filing",), domain: str = "financial_capacity",
             published: str | None = None) -> dict[str, Any]:
    return {"finding_id": fid, "statement": statement, "period_key": period_key,
            "source_kinds": list(kinds), "domain": domain,
            "source_published_at": published}


def _industrial(**chair: Any) -> dict[str, Any]:
    return {
        "findings": [
            _finding(SPIN, "Risk factors were updated for Altavia Technologies after the "
                     "Aerospace Spin-Off completed June 29, 2026, a structural change to "
                     "the reporting entity.", period_key="2026-Q2",
                     domain="counter_thesis"),
            _finding(DEBT, "FY2025 Group total debt was USD 34,580m (short-term 7,439m; "
                     "long-term 27,141m) against cash of 12,487m and equity of 13,904m.",
                     period_key="FY2025"),
            _finding(BACKLOG_OLD, "[single source] Altavia total backlog $38.3bn at 31 Mar "
                     "2026, up 15% vs 31 Mar 2025", kinds=("secondary_web",),
                     domain="capital_allocation", published="2026-03-31"),
            _finding(BACKLOG_Q2, "Altavia's remaining performance obligations were $38,008 "
                     "million as of 30 June 2026.", period_key="2026-Q2",
                     domain="growth_pipeline"),
            _finding(BACKLOG_Q3, "Altavia's remaining performance obligations were $18,798 "
                     "million as of 30 September 2026.", period_key="2026-Q3",
                     domain="growth_pipeline"),
            _finding(INDUSTRY, "[reported in the press] Industry backlogs reached $1,000bn "
                     "in 2025.", kinds=("secondary_web",), domain="industry_economics",
                     published="2026-03-01"),
        ],
        "chair": {
            "synthesis": chair.get("synthesis", (
                "Operating cash flow conversion was strong. Total debt stood at $34.6 "
                "billion against cash of $12.5 billion, reflecting a leveraged balance "
                "sheet. Guidance for Q4 2026 suggests moderate growth.")),
            "key_points": chair.get("key_points", [
                {"text": "Backlog as of March 31, 2026 was $38.3bn, up 15% year-over-year, "
                         "supporting future revenue visibility.", "finding_id": BACKLOG_OLD},
                {"text": "Remaining performance obligations were $18,798m at 30 September "
                         "2026.", "finding_id": BACKLOG_Q3},
            ]),
        },
    }


class TestPerimeter:
    def test_a_completed_dated_spin_off_in_an_issuer_filing_is_the_change(self) -> None:
        (event,) = pm.change_events(_industrial(), as_of=NOW.date())
        assert event["date"] == "2026-06-29"
        assert event["label"] == "[before the 29 Jun 2026 separation]"

    def test_a_planned_web_or_undated_separation_is_not_a_change(self) -> None:
        for statement, kinds in (
            ("The company expects to complete the spin-off of Aerospace by June 29, 2026.",
             ("issuer_filing",)),
            ("The Aerospace Spin-Off completed June 29, 2026.", ("secondary_web",)),
            ("The Aerospace Spin-Off was completed.", ("issuer_filing",)),
            ("Employee separation costs were completed in June 30, 2026.",
             ("issuer_filing",)),
        ):
            payload = {"findings": [_finding(SPIN, statement, kinds=kinds)]}
            assert pm.change_events(payload, as_of=NOW.date()) == [], statement

    def test_pre_change_levels_are_labelled_and_post_and_boundary_ones_are_not(self) -> None:
        out, record = pm.apply_perimeter_guard(_industrial(), as_of=NOW)
        by_id = {f["finding_id"]: f for f in out["findings"]}
        label = "[before the 29 Jun 2026 separation]"
        assert by_id[DEBT]["statement"].startswith(label)
        assert by_id[DEBT]["perimeter"] == pm.CLASS_PRE
        # After the platform's own source label.
        assert by_id[BACKLOG_OLD]["statement"].startswith(f"[single source] {label}")
        # The first period end after the change: which perimeter is unknown → no label.
        assert label not in by_id[BACKLOG_Q2]["statement"]
        assert label not in by_id[BACKLOG_Q3]["statement"]
        assert label not in by_id[INDUSTRY]["statement"]
        chair = out["chair"]
        assert f"{label} Total debt stood at $34.6 billion" in chair["synthesis"]
        assert chair["synthesis"].startswith("Operating cash flow conversion was strong.")
        assert chair["key_points"][0]["text"].startswith(label)
        assert not chair["key_points"][1]["text"].startswith(label)
        assert {a["action"] for a in record["actions"]} == {pm.ACTION_LABELLED}

    def test_a_comparison_across_the_change_is_withheld(self) -> None:
        payload = _industrial(synthesis=(
            "Backlog fell from $38.3bn at 31 March 2026 to $18.8bn at 30 September 2026. "
            "Guidance is unchanged."))
        payload["findings"].append(_finding(
            "a1000000-0000-0000-0000-000000000007",
            "Total debt declined to USD 16,593m at 30 September 2026 from USD 34,580m at "
            "31 December 2025.", period_key="2026-Q3"))
        out, record = pm.apply_perimeter_guard(payload, as_of=NOW)
        assert out["chair"]["synthesis"].startswith("[Withheld at report assembly:")
        assert out["chair"]["synthesis"].endswith("Guidance is unchanged.")
        withheld = next(f for f in out["findings"]
                        if f["finding_id"].startswith("a1000000"))
        assert withheld["perimeter_withheld"] is True
        assert "34,580" not in withheld["statement"]
        assert pm.ACTION_WITHHELD in {a["action"] for a in record["actions"]}

    def test_a_comparison_the_text_puts_on_a_comparable_basis_is_kept(self) -> None:
        text = ("On a comparable basis, backlog fell from $38.3bn at 31 March 2026 to "
                "$18.8bn at 30 September 2026.")
        out, _ = pm.apply_perimeter_guard(_industrial(synthesis=text), as_of=NOW)
        assert out["chair"]["synthesis"] == text

    def test_no_change_no_edit(self) -> None:
        payload = _industrial()
        payload["findings"] = payload["findings"][1:]
        out, record = pm.apply_perimeter_guard(payload, as_of=NOW)
        assert out == payload and record["changes"] == [] and record["actions"] == []

    def test_an_sec_restatement_marks_the_annual_perimeter_old(self) -> None:
        payload = _industrial()
        payload["findings"] = payload["findings"][1:]
        payload["financial_statements_state"] = {
            "reporting_periods": {"latest_annual": "FY2025"}}
        basis = [{"kind": "perimeter_recast", "period_end": "2025-09-30",
                  "restating_filed": "2026-10-22"}]
        out, record = pm.apply_perimeter_guard(payload, as_of=NOW, basis_changes=basis)
        (change,) = record["changes"]
        assert change["source"] == pm.SOURCE_SEC_RECAST
        debt = next(f for f in out["findings"] if f["finding_id"] == DEBT)
        assert debt["statement"].startswith("[before a change of perimeter")
        # A latest annual period that does not predate the 10-Q (a mislabelled FY2026)
        # leaves only the restated quarter old: FY2025 is no longer certainly old.
        payload["financial_statements_state"]["reporting_periods"]["latest_annual"] = "FY2026"
        out, _ = pm.apply_perimeter_guard(payload, as_of=NOW, basis_changes=basis)
        debt = next(f for f in out["findings"] if f["finding_id"] == DEBT)
        assert not debt["statement"].startswith("[before")

    def test_backlog_family(self) -> None:
        assert "backlog" in pm.level_families("RPO was $18.8bn")
        assert "backlog" in pm.level_families("the order book stood at $2bn")
        assert "backlog" not in pm.level_families("rpo is lowercase here")


# ── §13 entity firewall ──────────────────────────────────────────────────── #


class TestCurrentDevelopments:
    def test_an_industrials_namesake_registrant_headlines_are_withheld(self) -> None:
        names = sm.SubjectNames.of("Altavia International Inc.", ("ALT",),
                                   ("altavia aerospace",))
        items = [
            {"title": "Altavia Aerospace says it will invest $500 million in a new "
                      "production facility in Arizona", "domain": "defence-industry.example"},
            {"title": "$500M Aerospace Machining Expansion Starting | Altavia Aerospace | "
                      "American Machinist", "domain": "machinist.example"},
            {"title": "Altavia Aerospace selected for USD1.5 billion DHS counter-UAS "
                      "contract", "domain": "airspace.example"},
            {"title": "Altavia raises 2026 outlook after Q3", "domain": "news.example"},
            {"title": "Altavia completes spin-off of Altavia Aerospace", "domain": "x.example"},
        ]
        kept, withheld = sm.filter_current_developments(items, names)
        assert [k["title"][:15] for k in kept] == ["Altavia raises ", "Altavia complet"]
        assert {w["reason"] for w in withheld} == {sm.REASON_TITLE_ABOUT_RELATED_REGISTRANT}
        assert len(withheld) == 3

    @pytest.mark.parametrize(
        ("subject", "ticker", "title", "kept"),
        [
            ("LINDARA RESOURCES LIMITED", "LDR", "LINDARA ACQUIRES REMAINING 49% INTEREST "
             "FOR 100% OWNERSHIP IN HYDROMET FACILITY | INN", True),
            ("LINDARA RESOURCES LIMITED", "LDR",
             "Toyota: Expansion Through New Factory Construction Projects", False),
            ("LINDARA RESOURCES LIMITED", "LDR",
             "Oil & Gas Virtual Investor Conference Presentations Now Available", False),
            ("LINDARA RESOURCES LIMITED", "LDR",
             "Goldgroup Provides Update on Technical Studies, Exploration and Drilling", False),
            ("Vexgene SA", "VXG", "Global Hemophilia B Gene Therapy Market Report 2026 | "
             "Pfizer, CSL Behring, Bayer, and REGENXBIO", False),
            ("Vexgene SA", "VXG", "SANOFI Cours Action SAN, Cotation Bourse", False),
            ("Vexgene SA", "VXG", "Vexgene S.A. (VXG) Q1 2026 Earnings Call Transcript", True),
            ("PayLane Holdings, Inc.", "PYLN",
             "Kalshi Review (Nov 2026): How It Works, What It Costs", False),
            ("PayLane Holdings, Inc.", "PYLN",
             "Stripe Eyes PayLane Acquisition in Deal That Could Reshape Payments", True),
            ("PayLane Holdings, Inc.", "PYLN", "PYLN beats Q3 estimates", True),
        ],
    )
    def test_a_title_must_name_the_subject(self, subject, ticker, title, kept) -> None:  # noqa: ANN001
        names = sm.SubjectNames.of(subject, (ticker,))
        assert (sm.current_development_verdict(title, names) is None) is kept

    def test_the_issuers_own_verified_page_is_the_subjects(self) -> None:
        # A verified issuer origin (``issuer:<company id>``) is the subject's, whatever
        # its title says; a press-release CLASS alone, or another issuer's origin, is not.
        cid = uuid.uuid4()
        names = sm.SubjectNames.of("Vexgene SA", ("VXG",))
        items = [
            {"title": "Q3 2026 business update", "origin_key": f"issuer:{cid}",
             "source_class": "company_press_release"},
            {"title": "Q3 2026 business update", "origin_key": f"issuer:{uuid.uuid4()}",
             "source_class": "company_press_release"},
            {"title": "Q3 2026 business update", "origin_key": "news.example",
             "source_class": "company_press_release"},
        ]
        kept, withheld = sm.filter_current_developments(items, names, issuer_key=cid)
        assert [k["origin_key"] for k in kept] == [f"issuer:{cid}"]
        assert len(withheld) == 2
        # No issuer key: nothing is the subject's own (fail closed).
        kept, _ = sm.filter_current_developments(items, names)
        assert kept == []

    @pytest.mark.parametrize(("subject", "title", "kept"), [
        ("Amazon.com, Inc.", "Whole Foods opens 40 new stores", True),
        ("Rio Tinto Group", "Rio Tinto Limited lifts iron ore guidance", True),
        ("Alphabet Inc.", "Waymo expands to two cities", True),
        ("Alphabet Inc.", "Whole Foods opens 40 new stores", False),
    ])
    def test_a_listed_brand_names_its_parent(self, subject, title, kept) -> None:  # noqa: ANN001
        names = sm.SubjectNames.of(subject, ())
        assert (sm.current_development_verdict(title, names) is None) is kept

    def test_a_name_in_initials_or_run_together_still_names_the_subject(self) -> None:
        names = sm.SubjectNames.of("NORVANCO CHASE & CO", ("NVC",))
        for title in ("N.V. Norvanco Releases 2027 Capital Market Assumptions",
                      "Ohio State Athletics and NorvancoChase Announce Partnership"):
            assert sm.current_development_verdict(title, names) is None, title
        # A short alias is never read inside another word.
        assert not sm.names_subject("Chased by rivals", sm.SubjectNames.of("Chase Ltd"))

    def test_no_name_to_tell_the_subject_apart_judges_nothing(self) -> None:
        assert sm.current_development_verdict("Anything", sm.SubjectNames.of(None)) is None

    async def test_the_pipeline_filters_the_web_summary(self) -> None:
        from app.services.pipeline.v3_pipeline import _subject_current_developments

        company = SimpleNamespace(name="LINDARA RESOURCES LIMITED", ticker="LDR",
                                  exchange="ASX")
        summary = {"catalyst_evidence": [
            {"title": "LINDARA ACQUIRES REMAINING 49% INTEREST", "domain": "inn.example"},
            {"title": "Toyota: Expansion Through New Factory Construction Projects",
             "domain": "construction.example"},
        ], "label": "x"}
        out = await _subject_current_developments(summary, company)
        assert [i["title"][:7] for i in out["catalyst_evidence"]] == ["LINDARA"]
        assert out["catalyst_evidence_withheld"][0]["reason"] == \
            sm.REASON_TITLE_NAMES_NOT_SUBJECT
        assert summary["catalyst_evidence"][1]["title"].startswith("Toyota")  # not mutated
        clean = {"catalyst_evidence": summary["catalyst_evidence"][:1]}
        assert await _subject_current_developments(clean, company) is clean


class TestImplicitSubjectRules:
    NAMES = sm.SubjectNames.of("Vexgene SA", ("VXG",))

    def test_a_writer_declared_other_company_is_not_the_subject(self) -> None:
        statement = ("Calyxt, a separate company referenced in Vexgene filings, is described "
                     "as producing plant and seed harvests through seed production.")
        assert sm.declared_other_company(statement, self.NAMES) == "Calyxt"
        assert sm.declared_other_company(
            "Vexgene, an independent company since 2014, raised $50m.", self.NAMES) is None
        assert sm.declared_other_company("Revenue was $5m.", self.NAMES) is None

    def test_an_unnamed_subject_on_a_page_that_never_names_it_is_unresolved(self) -> None:
        statement = ("FDA placed two Regenxbio gene therapies on clinical hold after a trial "
                     "patient developed a brain tumor")
        page = "The FDA placed two of the company's programmes on hold, it said on Tuesday."
        assert sm.implicit_subject_unresolved(statement, [(page, False)], self.NAMES)
        # The page names the subject, or the subject owns it, or the statement names it.
        assert not sm.implicit_subject_unresolved(
            statement, [(page + " Vexgene shares fell.", False)], self.NAMES)
        assert not sm.implicit_subject_unresolved(statement, [(page, True)], self.NAMES)
        assert not sm.implicit_subject_unresolved(
            "VXG said the hold does not affect it.", [(page, False)], self.NAMES)

    def test_the_namesakes_name_is_not_the_subjects(self) -> None:
        names = sm.SubjectNames.of("Altavia International Inc.", ("ALT",),
                                   ("altavia aerospace",))
        assert not sm.names_subject("Altavia Aerospace won a contract", names)
        assert sm.names_only_related_registrant("Altavia Aerospace won a contract", names)
        assert sm.names_subject("Altavia and Altavia Aerospace signed", names)


COMPANY_ID = uuid.uuid4()


def _web(cid: str, text: str, *, title: str | None = None) -> Any:
    from app.services.agents.investigator import _Evidence

    body: dict[str, Any] = {"evidence_id": cid, "text": text}
    if title:
        body["title"] = title
    return _Evidence(citation_id=cid, kind="search_company_corpus", text=json.dumps(body),
                     web=True, origin_key="news.example", source_class="unknown_web",
                     period_key=None, scope_key=None)


class _Client:
    def __init__(self, findings: list[tuple[str, list[str]]]) -> None:
        self._payload = {"findings": [
            {"statement": s, "evidence_ids": ids, "direction": "neutral"}
            for s, ids in findings]}
        self.last_response_truncated = False

    async def complete_json(self, system: str, user: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        return self._payload


async def _run(findings: list[tuple[str, list[str]]], evidence: list[Any], *,
               domain: str = "risks") -> tuple[Any, list[Any]]:
    from app.services.agents.investigator import LLMInvestigator
    from app.services.director.planner import PlannedQuestion

    inv = LLMInvestigator(session=object(), company_id=COMPANY_ID, ticker="VXG",
                          exchange="EPA", client=_Client(findings), company_name="Vexgene SA")
    question = PlannedQuestion(
        key="material_risks", text="What are the material risks?", origin="playbook",
        required_tools=frozenset({"search_company_corpus"}), domain=domain)
    found, _gaps, _answered = await inv._write_up("risk_analyst", question, evidence)
    return inv, found


class TestInvestigatorFailsClosed:
    async def test_an_implicit_subject_on_another_companys_page_is_not_attributed(
        self,
    ) -> None:
        evidence = [
            _web("ev:c:hold", "The FDA placed two of the company's gene therapies on "
                 "clinical hold after a trial patient developed a brain tumor.",
                 title="FDA halts two gene therapy trials"),
            _web("ev:c:own", "Vexgene reported cash of EUR 61.5 million at year end.",
                 title="Vexgene full-year results"),
        ]
        inv, findings = await _run([
            ("FDA placed two gene therapies on clinical hold after a trial patient "
             "developed a brain tumor.", ["ev:c:hold"]),
            ("Cash was EUR 61.5 million at year end.", ["ev:c:own"]),
        ], evidence)
        assert [f.statement for f in findings] == ["Cash was EUR 61.5 million at year end."]
        assert inv.diagnostics.statements_subject_unresolved == 1

    async def test_a_page_titled_with_the_subject_lends_it_its_subject(self) -> None:
        evidence = [_web("ev:c:t", "The company placed its lead programme on hold.",
                         title="Vexgene pauses lead programme")]
        inv, findings = await _run(
            [("The lead programme was placed on hold.", ["ev:c:t"])], evidence)
        assert len(findings) == 1
        assert inv.diagnostics.statements_subject_unresolved == 0

    async def test_an_industry_question_is_exempt(self) -> None:
        evidence = [_web("ev:c:i", "Gene therapy markets grew 30% in 2025.")]
        _inv, findings = await _run(
            [("Gene therapy markets grew 30% in 2025.", ["ev:c:i"])], evidence,
            domain="industry_economics")
        assert len(findings) == 1

    async def test_a_writer_declared_other_company_is_dropped(self) -> None:
        evidence = [_web("ev:c:c", "Calyxt produces seed through third parties. Vexgene "
                         "holds shares in Calyxt.", title="Vexgene and Calyxt")]
        inv, findings = await _run(
            [("Calyxt, a separate company referenced in Vexgene filings, produces seed "
              "through third parties.", ["ev:c:c"])], evidence, domain="operations_assets")
        assert findings == []
        assert inv.diagnostics.statements_declared_other_company == 1

    @pytest.mark.parametrize("statement", [
        "Autodesk reported revenue growth of 12%.",
        "The company's supplier Broadcom reported revenue growth of 12%.",
    ])
    async def test_a_named_or_company_subject_is_not_implicit(self, statement: str) -> None:
        evidence = [_web("ev:c:n", "Revenue growth of 12%.", title="Software peers")]
        inv, findings = await _run([(statement, ["ev:c:n"])], evidence)
        assert inv.diagnostics.statements_subject_unresolved == 0

    async def test_a_filing_is_left_to_the_registrant_guard(self) -> None:
        # Another registrant's filing reached by corpus search is a third-party filing
        # (ADR-079), not open-web prose: rule 2 never drops it.
        from dataclasses import replace

        item = replace(_web("ev:c:f", "Revenue rose on subscription growth."),
                       edgar_registrant="other")
        inv, findings = await _run(
            [("Revenue rose on subscription growth.", ["ev:c:f"])], [item])
        assert inv.diagnostics.statements_subject_unresolved == 0


# ── Linear time ───────────────────────────────────────────────────────────── #


PATTERNS = [
    pm._EVENT_RE, pm._SEPARATION_RE, pm._COMPLETED_RE, pm._NOT_YET_RE, pm._BACKLOG_RE,
    pm._RPO_RE, pm._CHANGE_RE, pm._COMPARABLE_RE, pm._OWN_LABEL_RE, sm._DECLARED_OTHER_RE,
]
DEGENERATE = [" " * 10000, "\n" * 10000, "A-" * 3000, "a," * 5000, "a." * 3000,
              "Aa " * 4000, "[before the " * 2000, "Ab, a " * 2000]


@pytest.mark.parametrize("pattern", PATTERNS, ids=lambda p: p.pattern[:30])
def test_new_patterns_are_linear(pattern) -> None:  # noqa: ANN001
    for text in DEGENERATE:
        start = time.perf_counter()
        list(pattern.finditer(text))
        assert time.perf_counter() - start < 0.2


def test_the_guards_are_fast_on_degenerate_text() -> None:
    names = sm.SubjectNames.of("Vexgene SA", ("VXG",), ("vexgene bio",))
    start = time.perf_counter()
    for text in DEGENERATE:
        sm.names_subject(text, names)
        sm.declared_other_company(text, names)
        sm.current_development_verdict(text, names)
        pm.apply_perimeter_guard(_industrial(synthesis=text[:5000]), as_of=NOW)
    assert time.perf_counter() - start < 5.0
