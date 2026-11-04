"""Research Integrity Closure E3 — finding semantics (ADR-088).

Four defects from the 2026-11 QA campaign, each a regression here, and every rule the fix
rests on:

* **ADBE-3** — a figure for a fiscal year that had not ended ("Trefis reports Adobe
  FY2026 total revenue 26.58 Bil", run on 1 November; Adobe's FY2026 ends late November)
  was stored as a result. Every finding now carries a ``statement_kind`` declared at
  extraction, held to one structural invariant (``statement_kind.decide``). The shelved
  attempt (ADR-072) read the kind from prose after the fact and dropped good findings; so
  the tests below pin the DIRECTIONS that went wrong there as much as the defect.
* **LIN-1** — the synthesis said a pre-revenue rare-earth developer "mines and sells
  monazite concentrate". Interest income is now not operating revenue, the stage
  classifier names a pre-commercial stage, and the synthesis gate withholds a commercial-
  operation claim no issuer finding backs (``commercial_claims``).
* **PYPL-4** — a payment institution's long-term debt against cash was called "a
  manageable net debt position". Customer funds are now a business-model signal whose
  not-meaningful declaration (ADR-069) holds whatever the methodology.
* **ADBE-2** — a filing copy fetched by the web leg was "reported in the press; a filing
  figure differs". A page whose own text is a filing is a filing; the badge compares
  like with like only.
"""

from __future__ import annotations

import time
import uuid
from datetime import date
from typing import Any

import pytest

from app.services import commercial_claims as cc
from app.services import statement_kind as sk
from app.services.classification import stage as st
from app.services.playbooks import applicability as ap
from app.services.web_research import trust

# Fixture calendars. Issuer names appear in tests only.
ADOBE = sk.FiscalYearAnchor(2025, date(2025, 11, 28))  # 52/53-week, late November
APPLE = sk.FiscalYearAnchor(2025, date(2025, 9, 27))  # 52/53-week, late September
JUNE = sk.FiscalYearAnchor(2025, date(2025, 6, 30))  # a June year end
SEPTEMBER = sk.FiscalYearAnchor(2025, date(2025, 9, 30))  # a September year end
JANUARY = sk.FiscalYearAnchor(2025, date(2026, 1, 31))  # a retailer's January year end
AS_OF = date(2026, 11, 1)

ADBE_F14 = (
    "Trefis reports Adobe FY2026 total revenue 26.58 Bil and ending ARR growth 10.2%, per "
    "its Q4 2026 summary."
)


def _decide(statement: str, *, kind: Any = None, period: Any = None,
            published: date | None = None, as_of: date = AS_OF,
            anchor: sk.FiscalYearAnchor | None = None) -> sk.KindDecision:
    return sk.decide(declared_kind=kind, declared_period=period, statement=statement,
                     published_at=published, as_of=as_of, anchor=anchor)


# ─────────────────────────────── ADBE-3: statement kind ──────────────────────────── #


class TestVocabulary:
    def test_the_closed_vocabulary(self) -> None:
        assert sk.STATEMENT_KINDS == {
            "actual", "guidance", "forecast", "target", "estimate", "unknown"}
        assert sk.NON_ACTUAL_KINDS == {"guidance", "forecast", "target", "estimate"}

    @pytest.mark.parametrize("raw,kind", [
        ("Actual", "actual"), ("reported", "actual"), ("outlook", "guidance"),
        ("consensus", "estimate"), ("projection", "forecast"), ("Target", "target"),
        ("???", None), (None, None),
    ])
    def test_synonyms_normalise(self, raw: Any, kind: str | None) -> None:
        assert sk.normalise_kind(raw) == kind

    @pytest.mark.parametrize("raw,key", [
        ("FY2026", "2026"), ("fy26", "2026"), ("Q4 FY2026", "2026-Q4"),
        ("2026-Q4", "2026-Q4"), ("H1 2026", "2026-H1"), ("fourth quarter of fiscal 2026",
                                                         "2026-Q4"),
        # Review P3: an estimate suffix and the year-first forms.
        ("FY2026E", "2026"), ("2026e", "2026"), ("fiscal 2026", "2026"),
        ("2026 fiscal year", "2026"), ("financial year 2026", "2026"),
    ])
    def test_declared_periods_parse(self, raw: str, key: str) -> None:
        period = sk.parse_declared_period(raw)
        assert period is not None and period.key == key

    @pytest.mark.parametrize("raw", ["9M 2026", "2025/26", "YTD", "", None, "next year"])
    def test_a_part_period_or_range_is_no_period(self, raw: Any) -> None:
        assert sk.parse_declared_period(raw) is None

    def test_a_calendar_declaration_is_calendar(self) -> None:
        period = sk.parse_declared_period("CY2026")
        assert period is not None and period.calendar


class TestTheInvariant:
    def test_adbe3_regression_an_unended_fiscal_year_is_withheld(self) -> None:
        decision = _decide(ADBE_F14, kind="actual", period="FY2026", anchor=ADOBE)
        assert decision.withheld
        assert decision.kind == "unknown"
        assert decision.basis == sk.BASIS_WITHHELD
        assert decision.period_end == date(2026, 11, 28)
        # The admin-surface reason carries the period and its end, never statement text.
        assert decision.basis_detail == "withheld_period_not_ended:2026@2026-11-28"
        assert "Trefis" not in decision.basis_detail

    def test_a_closed_period_stated_as_actual_stays_actual(self) -> None:
        decision = _decide("Adobe FY2025 revenue was $23.77bn.", kind="actual",
                           period="FY2025", anchor=ADOBE)
        assert (decision.kind, decision.withheld) == ("actual", False)

    def test_expects_fy2026_revenue_is_guidance_when_declared_actual(self) -> None:
        decision = _decide("Adobe expects FY2026 revenue of ~$23.7bn.", kind="actual",
                           period="FY2026", anchor=ADOBE)
        assert decision.kind == "guidance"
        assert decision.basis == sk.BASIS_RECLASSIFIED
        assert not decision.withheld

    def test_expects_fy2026_revenue_is_guidance_when_undeclared(self) -> None:
        decision = _decide("Adobe expects FY2026 revenue of ~$23.7bn.", anchor=ADOBE)
        assert (decision.kind, decision.basis) == ("guidance", sk.BASIS_FORWARD_CUE)

    def test_a_declared_forward_kind_is_kept_whatever_the_words(self) -> None:
        decision = _decide("FY2026 revenue $25bn.", kind="guidance", period="FY2026",
                           anchor=ADOBE)
        assert (decision.kind, decision.withheld) == ("guidance", False)

    def test_an_analyst_estimate_is_an_estimate(self) -> None:
        assert _decide("Analysts expect FY2027 revenue of $26bn.",
                       anchor=ADOBE).kind == "estimate"

    def test_a_target_is_a_target(self) -> None:
        assert _decide("The company targets FY2028 operating margin of 45%.",
                       anchor=ADOBE).kind == "target"

    def test_a_forward_cue_about_an_ended_period_is_history(self) -> None:
        # ADR-072's failure the other way round: "beat guidance" about FY2025 is a result.
        decision = _decide("FY2025 revenue beat guidance at $23.77bn.", anchor=ADOBE)
        assert decision.kind == "unknown" and not decision.withheld

    def test_unknown_with_an_unended_declared_period_and_no_cue_is_withheld(self) -> None:
        decision = _decide("Total revenue 26.58 Bil.", kind="unknown", period="FY2026",
                           anchor=ADOBE)
        assert decision.withheld

    def test_the_publication_date_is_the_reference_never_later_than_as_of(self) -> None:
        assert sk.reference_date(date(2026, 3, 1), AS_OF) == date(2026, 3, 1)
        assert sk.reference_date(date(2027, 3, 1), AS_OF) == AS_OF
        assert sk.reference_date(None, AS_OF) == AS_OF
        # FY2025 (ended 2025-11-28) stated from a document of October 2025: not ended then.
        decision = _decide("Adobe FY2025 revenue was $23.77bn.", kind="actual",
                           period="FY2025", published=date(2025, 10, 15), anchor=ADOBE)
        assert decision.withheld

    def test_without_the_issuers_calendar_fiscal_periods_are_judged_leniently(self) -> None:
        # FY2026 can have ended by 2025-12-31 under some naming: never called unended.
        decision = _decide(ADBE_F14, kind="actual", period="FY2026", anchor=None)
        assert decision.kind == "actual" and not decision.withheld
        # A calendar declaration needs no calendar.
        decision = _decide("Revenue 26.58 Bil.", kind="actual", period="CY2026")
        assert decision.withheld

    @pytest.mark.parametrize("statement,kind", [
        ("Management had set an FY2025 revenue range of $10.0-10.4bn in February 2025.",
         "guidance"),
        ("The original FY2025 revenue goal of $10bn was missed; revenue came in at $9.6bn.",
         "target"),
        ("FY2025 company outlook: revenue $10.0-10.4bn.", "guidance"),
        ("Street FY2025 EPS ahead of the print stood at $4.10.", "estimate"),
        ("Prior to results, analysts' FY2025 revenue number was $9.9bn.", "estimate"),
        ("The company had called for FY2025 revenue of $10.2bn.", "guidance"),
        ("Revenue was $1.0bn, up 5%.", "forecast"),
    ])
    def test_rereview_p1a_a_declared_forward_kind_is_kept_for_an_ended_period(
        self, statement: str, kind: str
    ) -> None:
        # Pre-results guidance or a consensus figure stays non-actual after the period
        # ends: it must never enter the actual-eligible group (gaps, contradictions, the
        # synthesis pick). Missing is acceptable; wrong is not.
        decision = _decide(statement, kind=kind, period="FY2025", published=date(2026, 3, 1),
                           as_of=date(2026, 11, 4),
                           anchor=sk.FiscalYearAnchor(2025, date(2025, 12, 31)))
        assert (decision.kind, decision.withheld) == (kind, False)
        assert sk.is_non_actual(decision.kind)

    def test_rereview_p1b_a_company_named_target_is_not_a_target_cue(self) -> None:
        decision = _decide("Target Corp expects FY2027 revenue of $110bn.",
                           as_of=date(2026, 11, 4))
        assert decision.kind == "guidance"
        assert _decide("The company targets FY2028 operating margin of 45%.",
                       anchor=ADOBE).kind == "target"

    @pytest.mark.parametrize("statement", [
        "Revenue for the year ended June 30, 2026 was $70bn; FY2027 outlook is $78bn.",
        "Revenue reached $12bn in 2026 so far and the company expects 2027 growth.",
        "Year-to-date revenue was $9bn; the company expects FY2027 revenue of $14bn.",
        "Revenue for the six months ended 30 June 2026 was $4bn; FY2027 guidance is $9bn.",
    ])
    def test_review_p2_a_dated_ended_period_makes_the_statement_mixed(
        self, statement: str
    ) -> None:
        for anchor in (JUNE, sk.FiscalYearAnchor(2025, date(2025, 12, 31))):
            decision = _decide(statement, as_of=date(2026, 11, 4), anchor=anchor)
            assert (decision.kind, decision.basis) == ("unknown", sk.BASIS_NOT_DECLARED)

    def test_the_anchor_is_wanted_only_when_it_can_change_the_verdict(self) -> None:
        assert sk.wants_anchor(sk.Period("annual", 2026), AS_OF)
        assert not sk.wants_anchor(sk.Period("annual", 2025), AS_OF)
        assert not sk.wants_anchor(sk.Period("annual", 2026, calendar=True), AS_OF)


class TestReplayFindingsKeepTheirKind:
    """Statements from the 17 stored reports the first design labelled wrongly."""

    def test_a_year_inside_a_date_is_no_period(self) -> None:
        # JPM F31: two balance dates, both past.
        statement = ("Firmwide estimated uninsured deposits were $1,743.9bn at June 30, 2026 "
                     "and $1,558.6bn at Dec 31, 2025.")
        assert _decide(statement, anchor=sk.FiscalYearAnchor(2025, date(2025, 12, 31))
                       ).kind == "unknown"

    def test_a_mixed_statement_keeps_its_declared_kind(self) -> None:
        # CLLS3 F16: an actual cash balance beside a runway forecast.
        statement = ("Q1 2025: issuer's own release states a $246 million cash position at "
                     "31 March 2025, runway into H2 2027")
        assert _decide(statement).kind == "unknown"
        # ANTO2 F4: an actual average beside consensus.
        statement = ("Copper averaged $4.15/lb in 2024 vs $3.85/lb in 2023 (+8%); consensus "
                     "$4.25/lb 2025, $4.50/lb 2026.")
        assert _decide(statement, as_of=date(2026, 11, 2)).kind == "unknown"

    def test_the_fallback_reads_the_run_date_not_a_page_date(self) -> None:
        # LIN F9: a government page dated 2018 stating 2024 demand.
        statement = ("Global primary aluminum demand reached an estimated 71.4 Mt in 2024, "
                     "up 2.1% on 2023, per canada.ca.")
        assert _decide(statement, published=date(2018, 1, 19)).kind == "unknown"

    def test_a_documents_forward_period_tag_does_not_withhold(self) -> None:
        # CLLS5 F15: the 6-K was stored with a bogus "2028-H2" tag (pre-ADR-080); the
        # tag is the document's, not the statement's, and is never judged.
        statement = ("Cellectis will exit lasme-cel and eti-cel development to focus "
                     "resources on in vivo gene editing assets.")
        decision = _decide(statement, published=date(2026, 9, 14), as_of=date(2026, 11, 3))
        assert not decision.withheld


class TestFiscalCalendars:
    def test_a_52_53_week_year_end_is_within_tolerance(self) -> None:
        # Apple-like: FY2026 projected 2026-09-27; actual may be 2026-09-26.
        assert APPLE.end_of(sk.Period("annual", 2026)) == date(2026, 9, 27)
        assert sk.not_ended_by(sk.Period("annual", 2026), date(2026, 9, 25), APPLE) is None
        assert sk.not_ended_by(sk.Period("annual", 2026), date(2026, 9, 10), APPLE) == date(
            2026, 9, 27)

    def test_a_53rd_week_does_not_make_an_ended_year_unended(self) -> None:
        anchor = sk.FiscalYearAnchor(2023, date(2023, 9, 30))
        # FY2024 actually ended 2024-09-28; projected 2024-09-30; a filing of 2024-10-31.
        assert sk.not_ended_by(sk.Period("annual", 2024), date(2024, 10, 31), anchor) is None
        assert sk.not_ended_by(sk.Period("annual", 2024), date(2024, 9, 29), anchor) is None

    def test_a_june_year_end(self) -> None:
        assert JUNE.end_of(sk.Period("annual", 2026)) == date(2026, 6, 30)
        assert JUNE.end_of(sk.Period("quarter", 2026, 3)) == date(2026, 3, 30)
        decision = _decide("Revenue was $70bn.", kind="actual", period="FY2026",
                           as_of=date(2026, 5, 1), anchor=JUNE)
        assert decision.withheld
        decision = _decide("Revenue was $70bn.", kind="actual", period="FY2026",
                           as_of=date(2026, 7, 31), anchor=JUNE)
        assert decision.kind == "actual"

    def test_a_september_year_end(self) -> None:
        assert SEPTEMBER.end_of(sk.Period("quarter", 2026, 1)) == date(2025, 12, 30)
        decision = _decide("Q1 FY2026 revenue was $1.2bn.", kind="actual", period="Q1 FY2026",
                           as_of=date(2026, 2, 1), anchor=SEPTEMBER)
        assert decision.kind == "actual"
        decision = _decide("Q2 FY2026 revenue was $1.2bn.", kind="actual", period="Q2 FY2026",
                           as_of=date(2026, 2, 1), anchor=SEPTEMBER)
        assert decision.withheld

    def test_an_early_year_end_projects_no_quarter(self) -> None:
        assert JANUARY.ends_early_in_the_year
        assert JANUARY.end_of(sk.Period("quarter", 2026, 2)) is None
        # Named both ways in the wild: FY2025 is judged on its EARLIER reading.
        assert JANUARY.year_end(2025) == date(2025, 1, 31)


# ──────────────────────────── ADBE-3: wiring through the run ──────────────────────── #


class TestTheAnchorLoader:
    """Review P3: ``load_fiscal_year_anchor`` — success, timeout, error, cache, and no
    read without an exchange. ``peers._facts_for`` is the only network seam."""

    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401
        from app.integrations import sec_fundamentals_normalizer

        sk.reset_cache()

        class _Normalised:
            period_basis = "annual"
            fiscal_year = 2025
            reporting_period_end = "2025-11-28"

        monkeypatch.setattr(sec_fundamentals_normalizer, "normalize_company_facts",
                            lambda facts, ticker: _Normalised())
        yield
        sk.reset_cache()

    def _facts(self, monkeypatch: pytest.MonkeyPatch, impl: Any) -> list[str]:  # noqa: ANN401
        from app.services.agent_tools import peers

        calls: list[str] = []

        async def _facts_for(ticker: str, exchange: Any, provider: Any) -> Any:  # noqa: ANN401
            calls.append(ticker)
            return await impl()

        monkeypatch.setattr(peers, "_facts_for", _facts_for)
        return calls

    async def test_success_and_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def ok() -> Any:  # noqa: ANN401
            return {"facts": {}}, None

        calls = self._facts(monkeypatch, ok)
        anchor = await sk.load_fiscal_year_anchor("ADBE", "NASDAQ")
        assert anchor == sk.FiscalYearAnchor(2025, date(2025, 11, 28))
        assert await sk.load_fiscal_year_anchor("adbe", "nasdaq") == anchor
        assert calls == ["ADBE"]

    async def test_a_timeout_is_no_anchor_and_is_cached(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        async def slow() -> Any:  # noqa: ANN401
            await asyncio.sleep(5)
            return {"facts": {}}, None

        calls = self._facts(monkeypatch, slow)
        monkeypatch.setattr(sk, "ANCHOR_READ_TIMEOUT_SECONDS", 0.05)
        started = time.perf_counter()
        assert await sk.load_fiscal_year_anchor("ADBE", "NASDAQ") is None
        assert time.perf_counter() - started < 2.0
        assert await sk.load_fiscal_year_anchor("ADBE", "NASDAQ") is None
        assert calls == ["ADBE"]

    async def test_an_error_is_no_anchor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def boom() -> Any:  # noqa: ANN401
            raise RuntimeError("network down")

        self._facts(monkeypatch, boom)
        assert await sk.load_fiscal_year_anchor("ADBE", "NASDAQ") is None

    async def test_no_ticker_or_no_exchange_reads_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def ok() -> Any:  # noqa: ANN401
            return {"facts": {}}, None

        calls = self._facts(monkeypatch, ok)
        assert await sk.load_fiscal_year_anchor(None, "NASDAQ") is None
        assert await sk.load_fiscal_year_anchor("MC", None) is None
        assert await sk.load_fiscal_year_anchor("MC", " ") is None
        assert calls == []


class _V2Client:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.last_response_truncated = False

    async def complete_json(self, system: str, user: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        self.system, self.user = system, user
        return self._payload


async def _write_up(payload: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
                    *, anchor: sk.FiscalYearAnchor | None = ADOBE,
                    notes: tuple[str, ...] = (), evidence: list[Any] | None = None,
                    company_name: str | None = None, reads: list[str] | None = None) -> Any:
    from app.services.agents import investigator as inv_mod
    from app.services.director.planner import PlannedQuestion

    async def _anchor(ticker: object, exchange: object) -> Any:  # noqa: ANN401
        if reads is not None:
            reads.append(str(ticker))
        return anchor

    monkeypatch.setattr(inv_mod, "_load_fiscal_anchor", _anchor)
    company_id = uuid.uuid4()
    client = _V2Client(payload)
    inv = inv_mod.LLMInvestigator(
        session=None, company_id=company_id, ticker="ADBE", exchange="US", client=client,
        as_of=AS_OF, company_notes=notes, company_name=company_name,
    )
    evidence = evidence if evidence is not None else [inv_mod._Evidence(
        citation_id="ev:c:own", kind="search_company_corpus",
        text='{"evidence_id": "ev:c:own", "text": "..."}', period_key="FY2025",
        scope_key="group", source_class="issuer_filing", company_id=str(company_id),
    )]
    question = PlannedQuestion(key="revenue_scale", text="How large is revenue?",
                               origin="base", required_tools=frozenset())
    findings, gaps, answered = await inv._write_up("financial_analyst", question, evidence)
    return inv, client, findings, answered


class TestTheInvestigatorDeclaresAndHolds:
    async def test_the_schema_and_prompt_ask_for_the_kind(self, monkeypatch) -> None:  # noqa: ANN001
        _inv, client, _f, _a = await _write_up({"findings": []}, monkeypatch)
        assert '"statement_kind"' in client.system and '"period"' in client.system
        assert "A period that has not ended cannot have an actual" in client.system

    async def test_adbe3_a_declared_actual_for_an_unended_year_is_withheld(
        self, monkeypatch  # noqa: ANN001
    ) -> None:
        inv, _c, findings, answered = await _write_up({"findings": [{
            "statement": ADBE_F14, "evidence_ids": ["ev:c:own"],
            "statement_kind": "actual", "period": "FY2026",
        }]}, monkeypatch)
        (finding,) = findings
        assert finding.withheld and finding.statement_kind == "unknown"
        assert finding.statement_kind_basis == "withheld_period_not_ended:2026@2026-11-28"
        assert inv.diagnostics.statements_withheld_period_not_ended == 1
        # A withheld statement answers nothing.
        assert answered is False

    @pytest.mark.parametrize("period", ["FY2026", "Q4 2026", "Q4 FY2026"])
    async def test_review_p1_adbe3_from_a_third_party_page_is_withheld(
        self, monkeypatch, period: str  # noqa: ANN001
    ) -> None:
        # F14's real source: a third-party web page, not the issuer's document. The
        # statement is ABOUT the subject, so the subject's calendar judges it.
        from app.services.agents import investigator as inv_mod

        web = inv_mod._Evidence(
            citation_id="ev:w:trefis", kind="search_company_corpus",
            text='{"evidence_id": "ev:w:trefis", "text": "Adobe FY2026 revenue 26.58 Bil"}',
            web=True, source_class="unknown_web", origin_key="trefis.com",
            url="https://www.trefis.com/stock/adbe", published_at=date(2026, 10, 30))
        reads: list[str] = []
        inv, _c, findings, answered = await _write_up({"findings": [{
            "statement": ADBE_F14, "evidence_ids": ["ev:w:trefis"],
            "statement_kind": "actual", "period": period,
        }]}, monkeypatch, evidence=[web], company_name="Adobe Inc.", reads=reads)
        (finding,) = findings
        assert reads == ["ADBE"]
        assert finding.withheld and finding.statement_kind == "unknown"
        assert finding.statement_kind_basis.startswith("withheld_period_not_ended:2026")
        assert answered is False

    @pytest.mark.parametrize("statement", [
        "Autodesk FY2026 revenue was $6.13bn, ahead of Adobe's growth rate.",
        "Autodesk reported FY2026 revenue of $6.13bn.",
        "The company's supplier Broadcom reported FY2026 revenue of $60bn.",
    ])
    async def test_rereview_p1b_a_peer_statement_stays_on_the_lenient_reading(
        self, monkeypatch, statement: str  # noqa: ANN001
    ) -> None:
        from app.services.agents import investigator as inv_mod

        web = inv_mod._Evidence(
            citation_id="ev:w:1", kind="search_company_corpus",
            text='{"evidence_id": "ev:w:1", "text": "x"}', web=True,
            source_class="unknown_web", origin_key="trefis.com", url="https://trefis.com/a",
            published_at=date(2026, 10, 30))
        _inv, _c, findings, _a = await _write_up({"findings": [{
            "statement": statement, "evidence_ids": ["ev:w:1"],
            "statement_kind": "actual", "period": "FY2026",
        }]}, monkeypatch, evidence=[web], company_name="Adobe Inc.")
        assert [(f.statement_kind, f.withheld) for f in findings] == [("actual", False)]

    @pytest.mark.parametrize("statement,name,ticker,opens", [
        ("Trefis reports Adobe FY2026 total revenue 26.58 Bil.", "Adobe Inc.", "ADBE", True),
        ("Adobe's FY2026 revenue was $23.7bn.", "Adobe Inc.", "ADBE", True),
        ("ADBE FY2026 revenue was $23.7bn.", "Adobe Inc.", "ADBE", True),
        ("Autodesk FY2026 revenue was $6.13bn, ahead of Adobe's growth rate.", "Adobe Inc.",
         "ADBE", False),
        ("The company's supplier Broadcom reported FY2026 revenue of $60bn.",
         "Alphabet Inc.", "GOOGL", False),
        ("Salesforce Ventures portfolio company Snowflake reported FY2026 revenue of $4.4bn.",
         "Salesforce, Inc.", "CRM", False),
        ("Walmart beat its FY2026 sales target with revenue of $700bn.",
         "Target Corporation", "TGT", False),
        ("Apple Hospitality REIT reported FY2026 revenue of $1.4bn.", "Apple Inc.", "AAPL",
         False),
        ("Target reported FY2026 sales of $107bn.", "Target Corporation", "TGT", True),
    ])
    def test_rereview_p1b_only_the_opening_subject_counts(
        self, statement: str, name: str, ticker: str, opens: bool
    ) -> None:
        from app.services.agents.investigator import statement_opens_with_subject

        assert statement_opens_with_subject(statement, name, ticker) is opens

    async def test_another_entitys_period_stays_lenient(self, monkeypatch) -> None:  # noqa: ANN001
        # A statement about someone else on a third-party page is never put on the
        # subject's calendar: Microsoft's FY2026 (a June year end) has ended.
        from app.services.agents import investigator as inv_mod

        web = inv_mod._Evidence(
            citation_id="ev:w:news", kind="search_company_corpus",
            text='{"evidence_id": "ev:w:news", "text": "Microsoft FY2026 revenue $281bn"}',
            web=True, source_class="unknown_web", origin_key="news.example",
            url="https://news.example/msft", published_at=date(2026, 10, 30))
        reads: list[str] = []
        _inv, _c, findings, _a = await _write_up({"findings": [{
            "statement": "Microsoft FY2026 revenue was $281bn.",
            "evidence_ids": ["ev:w:news"], "statement_kind": "actual", "period": "FY2026",
        }]}, monkeypatch, evidence=[web], company_name="Adobe Inc.", reads=reads)
        assert reads == []
        assert all(not f.withheld for f in findings)

    async def test_a_closed_year_stays_an_answer(self, monkeypatch) -> None:  # noqa: ANN001
        inv, _c, findings, answered = await _write_up({"findings": [{
            "statement": "Adobe FY2025 revenue was $23,769m.", "evidence_ids": ["ev:c:own"],
            "statement_kind": "actual", "period": "FY2025",
        }]}, monkeypatch)
        (finding,) = findings
        assert (finding.statement_kind, finding.withheld) == ("actual", False)
        assert answered is True

    async def test_guidance_written_as_actual_is_reclassified(self, monkeypatch) -> None:  # noqa: ANN001
        inv, _c, findings, _a = await _write_up({"findings": [{
            "statement": "Adobe expects FY2026 revenue of ~$23.7bn.",
            "evidence_ids": ["ev:c:own"], "statement_kind": "actual", "period": "FY2026",
        }]}, monkeypatch)
        (finding,) = findings
        assert finding.statement_kind == "guidance" and not finding.withheld
        assert inv.diagnostics.statements_reclassified_period_not_ended == 1

    async def test_company_notes_reach_the_prompt(self, monkeypatch) -> None:  # noqa: ANN001
        _i, client, _f, _a = await _write_up(
            {"findings": []}, monkeypatch, notes=("Not meaningful for this company: net debt.",))
        assert "COMPANY CONTEXT" in client.user and "net debt" in client.user

    async def test_no_notes_leave_the_prompt_unchanged(self, monkeypatch) -> None:  # noqa: ANN001
        _i, client, _f, _a = await _write_up({"findings": []}, monkeypatch)
        assert "COMPANY CONTEXT" not in client.user


class TestTheLedgerKeepsTheKind:
    @pytest.fixture
    async def session(self):  # noqa: ANN201
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.ext.compiler import compiles
        from sqlalchemy.pool import StaticPool

        from app.db.base import Base

        @compiles(JSONB, "sqlite")
        def _json(element, compiler, **kw):  # noqa: ANN001, ANN202
            return "JSON"

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                     connect_args={"check_same_thread": False})
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            yield s
        await engine.dispose()

    async def test_the_kind_and_its_basis_are_stored(self, session) -> None:  # noqa: ANN001
        from app.services.ledger import store as ledger

        run = await ledger.open_run(session, mode="standard")
        row = await ledger.record_finding(
            session, run, statement="Revenue 26.58 Bil.", evidence_ids=["ev:a"],
            statement_kind="unknown",
            statement_kind_basis="withheld_period_not_ended:FY2026@2026-11-28",
            verification_status="withdrawn",
        )
        assert row.statement_kind == "unknown"
        assert row.statement_kind_basis.startswith("withheld")
        assert row.verification_status == "withdrawn"

    async def test_a_kind_outside_the_vocabulary_is_refused(self, session) -> None:  # noqa: ANN001
        from app.services.ledger import store as ledger

        run = await ledger.open_run(session, mode="standard")
        with pytest.raises(ValueError):
            await ledger.record_finding(session, run, statement="x y z", evidence_ids=["e"],
                                        statement_kind="result")

    async def test_the_admin_record_lists_withheld_statements_by_id(self, session) -> None:  # noqa: ANN001
        from app.services.ledger import store as ledger
        from app.services.pipeline.v3_pipeline import _statement_kind_record

        run = await ledger.open_run(session, mode="standard")
        await ledger.record_finding(session, run, statement="A result.", evidence_ids=["e1"],
                                    statement_kind="actual", statement_kind_basis="model")
        await ledger.record_finding(session, run, statement="Guidance.", evidence_ids=["e2"],
                                    statement_kind="guidance",
                                    statement_kind_basis="reclassified_period_not_ended:x")
        withheld = await ledger.record_finding(
            session, run, statement="Secret text 26.58 Bil.", evidence_ids=["e3"],
            statement_kind="unknown", verification_status="withdrawn",
            statement_kind_basis="withheld_period_not_ended:FY2026@2026-11-28")
        record = await _statement_kind_record(session, run)
        assert record["counts"] == {"actual": 1, "guidance": 1}
        assert record["reclassified_period_not_ended"] == 1
        assert record["withheld_period_not_ended"] == [{
            "finding_id": str(withheld.id),
            "reason": "withheld_period_not_ended:FY2026@2026-11-28"}]
        assert "Secret" not in str(record)

    async def test_no_declared_kind_no_record(self, session) -> None:  # noqa: ANN001
        from app.services.ledger import store as ledger
        from app.services.pipeline.v3_pipeline import _statement_kind_record

        run = await ledger.open_run(session, mode="standard")
        await ledger.record_finding(session, run, statement="Old row.", evidence_ids=["e"])
        assert await _statement_kind_record(session, run) == {}


class TestDownstreamAnActualSlotTakesNoForwardStatement:
    def test_guidance_never_answers_a_reported_value_gap(self) -> None:
        from app.services.pipeline import gap_reconciliation as gr

        facts = gr.FindingFacts.from_row(type("Row", (), {
            "id": uuid.uuid4(), "statement": "FY2026 revenue $25,000m.", "period_key": "FY2026",
            "scope_key": "group", "evidence_ids_json": ["e"], "source_kinds_json":
                ["issuer_filing"], "verification_status": "unverified",
            "source_published_at": None, "superseded_by_finding_id": None,
            "statement_kind": "guidance", "claim_key": None, "question_key": None,
        })())
        assert facts.is_non_actual
        assert gr._asks_for_reported_value("metric:revenue")
        assert not gr._asks_for_reported_value("metric:capex_project")
        kwargs = dict(gap_text="Group revenue for FY2026", gap_project=None,
                      gap_period="FY2026", scope="group", as_of=date(2026, 12, 31))
        assert gr._assess(facts, "metric:revenue", **kwargs) is None
        from dataclasses import replace

        assert gr._assess(replace(facts, statement_kind="actual"), "metric:revenue",
                          **kwargs) is not None

    def test_a_forward_statement_never_takes_a_financial_statement_reading(self) -> None:
        support = [trust.SupportItem(evidence_id="w", source_class="major_financial_press",
                                     origin_key="reuters.com", web=True)]
        statement = "Adobe FY2026 revenue $26.58bn."
        assert trust.assess_claim(statement, support).claim_type == trust.CT_FINANCIAL_STATEMENT
        estimate = trust.assess_claim(statement, support, statement_kind="estimate")
        assert estimate.claim_type is None and estimate.label.startswith("estimate by")
        guidance = trust.assess_claim(statement, support, statement_kind="guidance")
        assert guidance.claim_type == trust.CT_GUIDANCE

    def test_a_forward_statement_is_marked_in_the_report_and_not_the_synthesis_pick(
        self,
    ) -> None:
        from app.services.pipeline import professional_research as pr

        actual = pr.FindingView(finding_id="a", statement="Revenue was $23,769m in FY2025.",
                                domain="business_model", question_key="q",
                                statement_kind="actual")
        guidance = pr.FindingView(
            finding_id="g", statement="FY2026 revenue $25,900m to $26,100m; EPS $20.",
            domain="business_model", question_key="q", statement_kind="guidance")
        assert "statement_kind" not in pr._finding_dict(actual, "F1")
        assert pr._finding_dict(guidance, "F2")["statement_kind"] == "guidance"
        sections = [{"title": "Business model", "findings": [
            pr._finding_dict(guidance, "F2"), pr._finding_dict(actual, "F1")]}]
        (sentence,) = pr.deterministic_synthesis(sections)
        assert sentence["labels"] == ["F1"]
        only = [{"title": "Outlook", "findings": [pr._finding_dict(guidance, "F2")]}]
        (sentence,) = pr.deterministic_synthesis(only)
        assert sentence["text"].startswith("Outlook: [company guidance] ")

    def test_council_inputs_carry_the_kind_only_when_declared(self) -> None:
        from app.services.council_v2.inputs import FindingRef

        base = dict(finding_id=uuid.uuid4(), statement="s", mechanism=None, direction=None,
                    confidence=None, evidence_ids=(), calculation_ids=(), period_key=None,
                    scope_key=None, originating_role=None, verification_status="unverified")
        assert "statement_kind" not in FindingRef(**base).to_dict()
        assert FindingRef(**base, statement_kind="forecast").to_dict()["statement_kind"] == (
            "forecast")


class TestSchema:
    def test_the_model_and_migration_agree(self) -> None:
        import importlib.util
        from pathlib import Path

        from app.models.ledger import ResearchFinding
        from app.services import schema_readiness

        columns = ResearchFinding.__table__.columns
        assert columns["statement_kind"].nullable and columns["statement_kind_basis"].nullable
        path = Path(__file__).resolve().parents[1] / (
            "alembic/versions/045_add_finding_statement_kind.py")
        spec = importlib.util.spec_from_file_location("m045", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        assert (module.revision, module.down_revision) == ("045", "044")
        assert set(schema_readiness.MIGRATION_045_COLUMNS["research_findings"]) == {
            "statement_kind", "statement_kind_basis"}


# ──────────────────────────────── LIN-1: business stage ─────────────────────────────── #

LINDIAN = {"name": "LINDIAN RESOURCES LIMITED", "ticker": "LIN", "exchange": "AU"}
LIN_SENTENCE = (
    "The company mines and sells monazite concentrate from its Kangankunde project in "
    "Malawi and owns downstream processing facilities in Kazakhstan, with a binding offtake "
    "agreement for a planned rare earth carbonate product"
)
LIN_FINDINGS = [
    {"label": "F3", "statement": "FY2026 Group revenue $2,453,519 = interest income only; "
     "Australia $2,453,418, Malawi $101, Tanzania and Guinea nil.",
     "source_kinds": ["issuer_filing"]},
    {"label": "F5", "statement": "Lindian mines and concentrates monazite at Kangankunde.",
     "source_kinds": ["issuer_filing"]},
]


class TestStage:
    def test_interest_income_is_not_operating_revenue(self) -> None:
        texts = ["The Group's revenue for the year comprised interest income only."]
        assert st.revenue_is_interest_only(texts)
        assert not st.revenue_is_interest_only(["Revenue grew; interest income rose."])

    def test_interest_only_revenue_proves_no_revenue_and_a_pre_revenue_stage(self) -> None:
        out = st.assess([], ["Our revenue was derived solely from interest income."],
                        has_commodity=False, issuer_name="Lindian Resources Limited")
        assert out.proofs["no_or_immaterial_revenue"] is True
        assert out.proofs["revenue_is_interest_only"] is True
        assert out.commercial_stage == st.STAGE_PRE_REVENUE
        assert out.to_dict()["commercial_stage"] == "pre_revenue"

    @pytest.mark.parametrize("text,stage", [
        ("We are a clinical-stage biotechnology company.", "clinical"),
        ("The Company has not yet commenced commercial production.", "pre_production"),
        ("We are an exploration and development company.", "development"),
        ("Lindian Resources is a pre-revenue rare earths developer.", "pre_revenue"),
        ("The Company is in the development stage.", "development"),
        ("We have not yet generated any revenue from product sales.", "pre_revenue"),
    ])
    def test_the_issuers_own_words_name_the_stage(self, text: str, stage: str) -> None:
        assert st.assess_commercial_stage(
            [text], issuer_name="Lindian Resources Limited", revenue_absent=True)[0] == stage

    def test_no_stage_without_p1_whatever_the_words(self) -> None:
        # Review P1: the self-description alone never sets a stage; operating revenue
        # must be absent or immaterial too.
        assert st.assess_commercial_stage(
            ["We are a clinical-stage biotechnology company."]) == (None, [])

    def test_commercial_production_started_cancels_it(self) -> None:
        texts = ["The Company is a pre-production company.",
                 "The Company declared commercial production in March."]
        assert st.assess_commercial_stage(texts, revenue_absent=True) == (None, [])

    def test_third_party_wording_is_not_the_issuers(self) -> None:
        assert st.assess_commercial_stage(
            ["Our customers are clinical-stage biotechnology companies."],
            revenue_absent=True)[0] is None

    @pytest.mark.parametrize("texts", [
        # Review P1 repros: a project, a pipeline, a plant or a cost line is not the
        # company — none of these sets a stage, even with P1 granted.
        ["The Group capitalised pre-production stripping costs of $120 million at our new "
         "pit.", "Our copper production was 664,000 tonnes in 2025."],
        ["Our development-stage projects include the Twin Metals project.",
         "The Group sold 650,000 tonnes of copper."],
        ["Our clinical-stage programs include three oncology candidates.",
         "Net product sales of our marketed product were $2.1 billion."],
        ["We are a commercial-stage biopharmaceutical company with a clinical-stage "
         "pipeline."],
        ["The Company has not yet commenced production at its new Ohio plant, which is "
         "expected to open in 2027.", "Revenue was $4.2 billion."],
        ["The Company is engaged in the acquisition, exploration and development of mineral "
         "properties and operates the producing Fruta del Norte mine."],
    ], ids=["pre-production-stripping", "development-stage-project", "clinical-programs",
            "commercial-stage-veto", "plant-not-commenced", "explorer-producer"])
    def test_review_p1_operating_companies_get_no_stage(self, texts: list[str]) -> None:
        for revenue_absent in (False, True):
            assert st.assess_commercial_stage(
                texts, issuer_name="Example Resources Inc",
                revenue_absent=revenue_absent) == (None, [])

    def test_review_p1_a_producer_with_revenue_facts_has_no_stage(self) -> None:
        facts = [
            {"field": field, "numeric_value": value, "period": f"FY{year}",
             "confidence": "high", "scope": "group", "document_id": "d1",
             "fact_id": f"{field}{year}", "currency": "USD", "scale": "units"}
            for year, revenue in ((2025, 6600e6), (2024, 6300e6))
            for field, value in (("revenue", revenue), ("net_income", 1200e6),
                                 ("operating_expenses", 4000e6))
        ]
        texts = ["The Group capitalised pre-production stripping costs of $120 million at "
                 "our new pit.", "We are a pre-production company."]
        out = st.assess(facts, texts, has_commodity=True, mining_sector=True,
                        issuer_name="Example Mining plc")
        assert out.proofs["no_or_immaterial_revenue"] is False
        assert out.commercial_stage is None
        assert "commercial_stage" not in out.to_dict()

    def test_an_operating_company_has_no_stage_and_an_unchanged_payload(self) -> None:
        out = st.assess([], ["We sell software subscriptions."], has_commodity=False)
        assert out.commercial_stage is None
        assert "commercial_stage" not in out.to_dict()


class TestCommercialOperationGate:
    def test_lin1_regression_the_claim_is_withheld(self) -> None:
        subject = {**LINDIAN, "commercial_stage": "pre_revenue"}
        assert cc.claims_commercial_operation(LIN_SENTENCE, subject)
        assert cc.sentence_blocked(LIN_SENTENCE, LIN_FINDINGS, subject)

    def test_no_stage_no_gate(self) -> None:
        assert not cc.sentence_blocked(LIN_SENTENCE, LIN_FINDINGS, LINDIAN)

    def test_an_issuer_statement_of_actual_sales_backs_it(self) -> None:
        backed = [*LIN_FINDINGS, {"statement": "Lindian sold 12,400 t of concentrate in FY2026.",
                                  "source_kinds": ["issuer_filing"]}]
        assert not cc.sentence_blocked(LIN_SENTENCE, backed,
                                       {**LINDIAN, "commercial_stage": "development"})

    @pytest.mark.parametrize("finding", [
        {"statement": "Lindian expects to sell 6,000 t a year from 2027.",
         "source_kinds": ["issuer_filing"]},
        {"statement": "Iluka sold 12,400 t in 2025.", "source_kinds": ["third_party_filing"]},
        {"statement": "Lindian has not sold 1 t yet.", "source_kinds": ["issuer_filing"]},
        {"statement": "Sales of 6,000 t a year.", "source_kinds": ["issuer_filing"],
         "statement_kind": "forecast"},
    ])
    def test_forward_negated_third_party_or_non_actual_statements_back_nothing(
        self, finding: dict[str, Any]
    ) -> None:
        assert not cc.backs_commercial_operation(finding)

    @pytest.mark.parametrize("sentence", [
        # Stored report sentences that must never be withheld (replay).
        "Lindian's Group operates a single segment focused on exploration and development "
        "of mineral projects, with revenue currently derived solely from interest income "
        "rather than mineral sales.",
        "Peer comparisons indicate Iluka as a large rare earths producer with evolving cost "
        "structures.",
        "Cellectis generates collaboration revenue from partnerships such as with "
        "AstraZeneca, which secured exclusive gene targets and candidate options.",
        "The company generates revenue from interest on its cash deposits.",
        "Its offtaker sells the product in China.",
        "The company does not yet produce or sell concentrate.",
        "The company plans to produce 6,000 t a year.",
    ])
    def test_sentences_that_are_not_a_commercial_claim(self, sentence: str) -> None:
        assert not cc.claims_commercial_operation(
            sentence, {**LINDIAN, "commercial_stage": "development"})

    @pytest.mark.parametrize("sentence", [
        "It sells its stake in the Zaldivar joint venture to its partner [F3].",
        "The company sells shares to fund the project [F4].",
        "The company is selling non-core assets [F5].",
        "The group produces annual sustainability reports [F6].",
    ])
    def test_review_p2_a_capital_or_reporting_act_is_no_commercial_claim(
        self, sentence: str
    ) -> None:
        subject = {"name": "Example Mining plc", "ticker": "EXM",
                   "commercial_stage": "pre_production"}
        assert not cc.claims_commercial_operation(sentence, subject)
        assert not cc.sentence_blocked(sentence, [], subject)

    @pytest.mark.parametrize("sentence", [
        "Example Mining produces copper at its Los Pelambres mine [F1].",
        "The company sells copper concentrate to smelters in Asia [F2].",
    ])
    def test_review_p2_a_product_is_still_a_commercial_claim(self, sentence: str) -> None:
        subject = {"name": "Example Mining plc", "ticker": "EXM",
                   "commercial_stage": "pre_production"}
        assert cc.claims_commercial_operation(sentence, subject)

    @pytest.mark.parametrize("statement", [
        "The company sold 650kt of copper in 2025 and expects 2026 production of 700kt.",
        "Copper production of 664kt in 2025.",
        "Net product sales were $2.1 billion in 2025.",
    ])
    def test_review_p2_actual_sales_back_even_beside_a_forward_clause(
        self, statement: str
    ) -> None:
        assert cc.backs_commercial_operation(
            {"statement": statement, "source_kinds": ["issuer_filing"]})

    def test_review_p2_a_forward_word_before_the_figure_still_backs_nothing(self) -> None:
        for statement in ("Production of copper and gold will reach 500koz in 2027.",
                          "Expected production of 700kt in 2026."):
            assert not cc.backs_commercial_operation(
                {"statement": statement, "source_kinds": ["issuer_filing"]})

    def test_the_editor_rejects_it_with_a_reason(self) -> None:
        from app.services.pipeline import professional_research as pr

        findings = {f["label"]: f for f in LIN_FINDINGS}
        ok, reason = pr.validate_sentence(
            "The company mines and sells monazite concentrate from its project [F5].",
            findings, {**LINDIAN, "commercial_stage": "pre_revenue"})
        assert (ok, reason) == (False, cc.REASON)
        ok, _ = pr.validate_sentence(
            "The company mines and sells monazite concentrate from its project [F5].",
            findings, LINDIAN)
        assert ok

    def test_the_deterministic_synthesis_skips_the_unbacked_claim(self) -> None:
        from app.services.pipeline import professional_research as pr

        sections = [{"title": "Products", "findings": [
            {**LIN_FINDINGS[1], "confidence": 0.9},
            {"label": "F6", "statement": "Kangankunde Mineral Resource 261 Mt at 2.14% TREO.",
             "source_kinds": ["issuer_filing"], "confidence": 0.5},
        ]}]
        (sentence,) = pr.deterministic_synthesis(
            sections, {**LINDIAN, "commercial_stage": "development"})
        assert sentence["labels"] == ["F6"]

    def test_the_chair_drops_the_claim(self) -> None:
        from app.services.agents.chair import ChairVerdict, _gate_material_events
        from app.services.council_v2.inputs import CouncilInput, FindingRef

        ref = FindingRef(finding_id=uuid.uuid4(), statement=LIN_FINDINGS[0]["statement"],
                         mechanism=None, direction=None, confidence=None,
                         evidence_ids=("e",), calculation_ids=(), period_key="FY2026",
                         scope_key="group", originating_role=None,
                         verification_status="unverified", source_kinds=("issuer_filing",))
        council = CouncilInput(research_run_id=uuid.uuid4(), convened=True, findings=(ref,))
        verdict = ChairVerdict(
            label="requires_more_evidence", fundamental_setup="mixed",
            synthesis=f"{LIN_SENTENCE}. Revenue is interest income only.",
            key_points=[{"text": LIN_SENTENCE, "finding_id": str(ref.finding_id)}],
        )
        out = _gate_material_events(verdict, council, {**LINDIAN, "commercial_stage":
                                                       "pre_revenue"})
        assert out.key_points == []
        assert out.synthesis == "Revenue is interest income only."


# ─────────────────────────────── PYPL-4: payment institutions ──────────────────────── #

PYPL_BALANCE_SHEET = [
    "Funds receivable and customer accounts 38,412 39,281",
    "Funds payable and amounts due to customers 41,532 40,107",
]


class TestPaymentInstitutions:
    def test_customer_funds_are_a_business_model_signal(self) -> None:
        holds, why = st.holds_customer_funds(PYPL_BALANCE_SHEET)
        assert holds and "funds_payable" in str(why)
        out = st.assess([], PYPL_BALANCE_SHEET, has_commodity=False)
        assert st.SIGNAL_CUSTOMER_FUNDS in out.signals

    def test_one_caption_or_a_licence_is_not_enough(self) -> None:
        assert not st.holds_customer_funds(["Customer balances rose."])[0]
        assert not st.holds_customer_funds(
            ["Customer balances are held under our money transmitter licenses."])[0]
        assert not st.holds_customer_funds(PYPL_BALANCE_SHEET[1:])[0]
        assert not st.holds_customer_funds(PYPL_BALANCE_SHEET[:1])[0]

    def test_a_caption_counts_only_as_a_row_with_a_figure(self) -> None:
        assert st.customer_funds_terms(
            ["Funds payable and amounts due to customers are described in Note 9."]) == []
        assert st.customer_funds_terms(PYPL_BALANCE_SHEET) == [
            ("customer_accounts", "asset"), ("funds_payable", "liability")]

    @pytest.mark.parametrize("texts", [
        ["We review customer balances for collectability each quarter.",
         "Our subsidiaries hold money transmitter licenses in 48 states for MoneyCenter "
         "services."],
        ["Allowance is based on aged customer account balances.",
         "Deposits include funds held for customers under construction contracts."],
        ["Customer accounts were £450bn.", "Customer balances grew 3%."],
        ["Funds held for clients were $30bn.", "Client fund obligations."],
    ], ids=["retailer-licence", "industrial-escrow", "uk-bank", "payroll-asset-only"])
    def test_review_p2_customer_funds_false_positives(self, texts: list[str]) -> None:
        assert st.holds_customer_funds(texts) == (False, None)

    def test_a_safeguarding_liability_with_an_asset_row_holds(self) -> None:
        assert st.holds_customer_funds([
            "Safeguarded customer funds 4,210 3,980",
            "Funds held for customers 4,305 4,012",
        ])[0]

    def test_a_negated_mention_states_nothing(self) -> None:
        assert st.customer_funds_terms(
            ["We do not hold funds payable and amounts due to customers of 1,000."]) == []

    def test_net_debt_is_not_meaningful_whatever_the_methodology(self) -> None:
        from app.services.playbooks.schema import PlaybookSelection

        out = ap.resolve(PlaybookSelection(playbooks=()),
                         signals=[st.SIGNAL_CUSTOMER_FUNDS])
        assert {"net_debt", "net_debt_to_ebitda"} <= out.calculations
        assert "current_ratio" in out.financial_categories
        assert "customer" in out.reader_note
        assert ap.resolve(PlaybookSelection(playbooks=())).is_empty

    def test_a_bank_keeps_its_declaration_and_gains_nothing_else(self) -> None:
        from app.services.playbooks import select

        selection = select(sector="Financials", industry="Banks")
        bank = ap.resolve(selection, sector="Financials", industry="Banks")
        both = ap.resolve(selection, sector="Financials", industry="Banks",
                          signals=[st.SIGNAL_CUSTOMER_FUNDS])
        assert both.calculations == bank.calculations
        assert both.financial_categories == bank.financial_categories
        assert both.analysed_by == bank.analysed_by

    @pytest.mark.parametrize("sentence", [
        "PayPal generated net cash provided by operating activities of $6.8bn [F2].",
        "Net cash from operations funds buybacks [F3].",
        "Net cash used in investing activities was $1.2bn [F4].",
    ])
    def test_review_p2_a_cash_flow_line_is_not_net_debt(self, sentence: str) -> None:
        subject = {"name": "PayPal Holdings", "ticker": "PYPL", "net_debt_not_meaningful": True}
        assert not ap.net_debt_sentence_blocked(sentence, subject)
        assert ap.net_debt_sentence_blocked(
            "The company has a manageable net debt position [F1].", subject)
        assert ap.net_debt_sentence_blocked("It holds a net cash position [F1].", subject)

    def test_pypl4_regression_the_synthesis_states_no_net_debt(self) -> None:
        sentence = ("Long-term debt at FY2025 was $9,987m against cash of $8,049m, reflecting "
                    "a manageable net debt position.")
        subject = {"name": "PayPal Holdings, Inc.", "net_debt_not_meaningful": True}
        assert ap.net_debt_sentence_blocked(sentence, subject)
        assert not ap.net_debt_sentence_blocked(sentence, {"name": "Honeywell"})
        assert not ap.net_debt_sentence_blocked("Cash was $8,049m.", subject)

    def test_the_editor_rejects_net_debt_with_a_reason(self) -> None:
        from app.services.pipeline import professional_research as pr

        findings = {"F1": {"label": "F1", "statement": "Long-term debt $9,987m.",
                           "source_kinds": ["issuer_filing"]}}
        ok, reason = pr.validate_sentence(
            "The company carries a manageable net debt position against its cash [F1].",
            findings, {"name": "PayPal", "net_debt_not_meaningful": True})
        assert (ok, reason) == (False, ap.NET_DEBT_REASON)

    def test_every_model_is_told(self) -> None:
        from app.services.pipeline.v3_pipeline import company_context_notes
        from app.services.playbooks.schema import PlaybookSelection

        applic = ap.resolve(PlaybookSelection(playbooks=()),
                            signals=[st.SIGNAL_CUSTOMER_FUNDS])
        (note,) = company_context_notes(applic, None)
        assert "net debt" in note and "customer" in note
        assert company_context_notes(ap.NONE, None) == ()
        (stage_note,) = company_context_notes(ap.NONE, "pre_revenue")
        assert "pre-revenue" in stage_note

    async def test_the_red_team_and_chair_prompts_carry_the_notes(self) -> None:
        from app.services.agents.chair import LLMChair
        from app.services.agents.red_team import LLMRedTeam
        from app.services.council_v2.inputs import CouncilInput, FindingRef

        ref = FindingRef(finding_id=uuid.uuid4(), statement="Long-term debt $9,987m.",
                         mechanism=None, direction=None, confidence=None,
                         evidence_ids=("e",), calculation_ids=(), period_key="FY2025",
                         scope_key="group", originating_role=None,
                         verification_status="unverified", statement_kind="actual")
        council = CouncilInput(research_run_id=uuid.uuid4(), convened=True, findings=(ref,))
        prompt = LLMChair(context_notes=("Not meaningful: net debt.",))._prompt(council)
        assert prompt.startswith("COMPANY CONTEXT") and '"statement_kind": "actual"' in prompt
        assert "COMPANY CONTEXT" not in LLMChair()._prompt(council)

        class _Client:
            async def complete_json(self, system: str, user: str, **kw: Any) -> dict:  # noqa: ANN401
                self.user = user
                return {"challenges": []}

        client = _Client()
        await LLMRedTeam(client=client, context_notes=("Not meaningful: net debt.",)).select(
            findings=[ref], max_challenges=2)
        assert client.user.startswith("COMPANY CONTEXT")


# ──────────────────────────────── ADBE-2: source kind ─────────────────────────────── #

EDGAR_ADBE_URL = (
    "https://www.sec.gov/Archives/edgar/data/796343/000079634326000010/adbe-20251128.htm"
)
TEN_K_HEAD = (
    "UNITED STATES SECURITIES AND EXCHANGE COMMISSION Washington, D.C. 20549 FORM 10-K "
    "ANNUAL REPORT PURSUANT TO SECTION 13 OR 15(d) For the fiscal year ended November 28, "
    "2025 Commission File Number: 0-15175 ADOBE INC. (Exact name of registrant as "
    "specified in its charter)"
)


class TestSourceKindByDocument:
    def test_a_filing_copy_is_recognised_by_its_own_first_page(self) -> None:
        from app.services.web_research.edgar_registrant import is_filing_copy_text

        assert is_filing_copy_text(TEN_K_HEAD)
        assert not is_filing_copy_text(
            "Adobe's Form 10-K, filed with the Securities and Exchange Commission, showed …")
        assert not is_filing_copy_text("x" * 7000 + TEN_K_HEAD)

    def test_the_header_cik_is_read_once(self) -> None:
        from app.services.web_research.edgar_registrant import edgar_header_cik

        assert edgar_header_cik("CENTRAL INDEX KEY: 0000796343") == "796343"
        assert edgar_header_cik(
            "CENTRAL INDEX KEY: 0000796343 … CENTRAL INDEX KEY: 0000320193") is None

    def test_ingest_classifies_by_document_not_by_channel(self) -> None:
        from app.services.web_research.classify import classify_source, with_filing_copy_signal

        own = classify_source("https://www.adobe.com/investor/10k-2025.pdf",
                              issuer_domains=("adobe.com",))
        assert with_filing_copy_signal(own, TEN_K_HEAD).source_class == "issuer_filing"
        mirror = classify_source("https://www.annualreports.example/adbe-10k.html")
        relabelled = with_filing_copy_signal(mirror, TEN_K_HEAD)
        assert relabelled.source_class == "regulatory_filing"
        assert relabelled.rule == "page_signal"
        assert with_filing_copy_signal(mirror, "A news story.") == mirror

    def test_review_p0_a_cik_printed_off_edgar_never_makes_the_copy_the_issuers(
        self,
    ) -> None:
        from app.services.agents.investigator import (
            EdgarSubject,
            _Evidence,
            apply_edgar_registrant,
            filing_copy_off_edgar,
        )

        subject = EdgarSubject(ciks=frozenset({"796343"}), other_ciks=None,
                               match_names=("Adobe Inc.",), tickers=("ADBE",))
        own = _Evidence(citation_id="ev:c:1", kind="search_company_corpus",
                        text="CENTRAL INDEX KEY: 0000796343 " + TEN_K_HEAD, web=True,
                        source_class="regulatory_filing",
                        url="https://www.annualreports.example/adbe-10k.html")
        assert filing_copy_off_edgar(own)
        (checked,) = apply_edgar_registrant([own], subject)
        # Anyone can print the subject's CIK: off EDGAR it is "unidentified", a
        # third-party filing — never the issuer's own.
        assert checked.edgar_registrant == "unidentified"
        assert checked.ref is None or checked.ref.source_kind == "third_party_filing"
        unknown = _Evidence(citation_id="ev:c:2", kind="search_company_corpus",
                            text="Note 7 — Revenue by segment …", web=True,
                            source_class="regulatory_filing",
                            url="https://www.annualreports.example/adbe-10k.html#p40")
        (kept,) = apply_edgar_registrant([unknown], subject)
        assert kept.edgar_registrant == "unidentified"

    def test_the_issuers_own_edgar_filing_is_not_the_press(self) -> None:
        from app.services.agents.investigator import _Evidence, _support_for

        company_id = uuid.uuid4()
        item = _Evidence(citation_id="ev:c:1", kind="search_company_corpus", text="{}",
                         web=True, source_class="regulatory_filing", origin_key="sec.gov",
                         edgar_registrant="own", url=EDGAR_ADBE_URL)
        (support,) = _support_for(["ev:c:1"], [item], company_id)
        assert support.origin_key == f"issuer:{company_id}"
        verdict = trust.assess_claim("FY2025 revenue was $23,769m.", [support],
                                     issuer_key=company_id)
        assert verdict.meets and verdict.label is None

    def test_review_p0_a_forged_filing_page_is_never_the_issuers(self) -> None:
        from app.services.agents.investigator import (
            EdgarSubject,
            _Evidence,
            _support_for,
            apply_edgar_registrant,
        )
        from app.services.web_research.classify import classify_source, with_filing_copy_signal

        page = ("UNITED STATES SECURITIES AND EXCHANGE COMMISSION Washington, D.C. 20549 "
                "FORM 10-K Commission File Number 0-15175 CENTRAL INDEX KEY: 0000796343 "
                "ADOBE INC. Total revenue for fiscal 2025 was $31,000 million.")
        url = "https://totally-not-sec.example/adbe/10k.html"
        before = classify_source(url, issuer_domains=("adobe.com",), licence_signals=())
        after = with_filing_copy_signal(before, page)
        # The page's words name the document kind, never its standing.
        assert after.source_class == "regulatory_filing"
        assert (after.tier, after.access_class) == (before.tier, before.access_class)
        assert after.tier != "T1_primary_filing"
        company_id = uuid.uuid4()
        subject = EdgarSubject(ciks=frozenset({"796343"}), other_ciks=None,
                               match_names=("Adobe Inc.",), tickers=("ADBE",))
        item = _Evidence(citation_id="ev:w:1", kind="search_company_corpus", text=page,
                         web=True, source_class=after.source_class,
                         origin_key="totally-not-sec.example", url=url)
        (judged,) = apply_edgar_registrant([item], subject)
        assert judged.edgar_registrant == "unidentified"
        (support,) = _support_for(["ev:w:1"], [judged], company_id)
        assert support.origin_key == "totally-not-sec.example"
        verdict = trust.assess_claim("Adobe FY2025 total revenue was $31,000m.", [support],
                                     issuer_key=company_id)
        assert not verdict.meets and verdict.label == trust.LABEL_FILING_NOT_VERIFIED
        # Even an item someone marked "own" is the issuer's only on EDGAR's address.
        forced = _Evidence(citation_id="ev:w:2", kind="search_company_corpus", text=page,
                           web=True, source_class="regulatory_filing",
                           origin_key="totally-not-sec.example", url=url,
                           edgar_registrant="own")
        (support,) = _support_for(["ev:w:2"], [forced], company_id)
        assert support.origin_key == "totally-not-sec.example"
        # And it never strips the real filing's canonical status.
        fake = trust.ClaimSide(
            finding_id="fake", statement="FY2025 revenue was $31,000m.",
            fields=("metric:revenue",), period="FY2025", scope_key="group",
            support=(trust.SupportItem(evidence_id="w", source_class="regulatory_filing",
                                       origin_key="totally-not-sec.example", web=True),))
        real = trust.ClaimSide(
            finding_id="real", statement="FY2025 revenue was $23,769m.",
            fields=("metric:revenue",), period="FY2025", scope_key="group",
            support=(trust.SupportItem(evidence_id="s", source_class="issuer_filing"),))
        (conflict,) = trust.find_contradictions(fake, [real])
        assert conflict.canonical_finding_id == "real"

    def test_an_unverified_filing_copy_is_labelled_as_one(self) -> None:
        support = trust.SupportItem(evidence_id="w", source_class="regulatory_filing",
                                    origin_key="annualreports.example", web=True)
        verdict = trust.assess_claim("FY2025 revenue was $23,769m.", [support])
        assert verdict.label == trust.LABEL_FILING_NOT_VERIFIED
        assert trust.unlabelled_statement(
            trust.labelled_statement("Revenue $1m.", trust.LABEL_FILING_NOT_VERIFIED)
        ) == "Revenue $1m."

    def test_a_filing_systems_copy_is_never_the_press_side_of_a_contradiction(
        self,
    ) -> None:
        filing = trust.ClaimSide(
            finding_id="a", statement="FY2025 revenue was $23,769m.", fields=("metric:revenue",),
            period="FY2025", scope_key="group",
            support=(trust.SupportItem(evidence_id="s", source_class="issuer_filing"),))
        copy = trust.ClaimSide(
            finding_id="b", statement="FY2025 revenue was $21,500m.", fields=("metric:revenue",),
            period="FY2025", scope_key="group",
            support=(trust.SupportItem(evidence_id="w", source_class="regulatory_filing",
                                       origin_key="sec.gov", web=True),))
        mirror = trust.ClaimSide(
            finding_id="m", statement="FY2025 revenue was $21,500m.", fields=("metric:revenue",),
            period="FY2025", scope_key="group",
            support=(trust.SupportItem(evidence_id="w", source_class="regulatory_filing",
                                       origin_key="annualreports.example", web=True),))
        # A mirror's page that merely looks like a filing is the press side (review P0).
        (conflict,) = trust.find_contradictions(mirror, [filing])
        assert conflict.canonical_finding_id == "a"
        press = trust.ClaimSide(
            finding_id="c", statement="FY2025 revenue was $21,500m.", fields=("metric:revenue",),
            period="FY2025", scope_key="group",
            support=(trust.SupportItem(evidence_id="p", source_class="major_financial_press",
                                       origin_key="reuters.com", web=True),))
        (conflict,) = trust.find_contradictions(copy, [filing])
        assert conflict.canonical_finding_id is None
        (conflict,) = trust.find_contradictions(press, [filing])
        assert conflict.canonical_finding_id == "a"

    def test_adbe2_regression_a_segment_list_is_not_compared_with_group_revenue(
        self,
    ) -> None:
        group = trust.ClaimSide(
            finding_id="g", statement="FY2025 revenue was $23,769m.", fields=("metric:revenue",),
            period="FY2025", scope_key="group",
            support=(trust.SupportItem(evidence_id="s", source_class="issuer_filing"),))
        segments = trust.ClaimSide(
            finding_id="s",
            statement=("Adobe's FY2025 revenue was split across three reportable segments: "
                       "Digital Media revenue $17,649m, Digital Experience revenue $5,864m."),
            fields=("metric:revenue",), period="FY2025", scope_key="group",
            support=(trust.SupportItem(evidence_id="w", source_class="unknown_web",
                                       web=True),))
        assert trust.find_contradictions(segments, [group]) == []


# ──────────────────────────────── Linear patterns ──────────────────────────────── #

ADVERSARIAL = [" " * 10000, "\n" * 10000, "A-" * 3000, "a," * 5000, "a." * 3000]


def _patterns() -> list[Any]:
    from app.services.agents import investigator as inv_mod
    from app.services.web_research import edgar_registrant as er

    subject_re = cc._subject_re({"name": "Lindian Resources Limited", "ticker": "LIN"})
    return [
        *sk._DECLARED_RES, sk._SEP_RE, sk._MENTION_RE, sk._ESTIMATE_RE, sk._TARGET_WORD_RE,
        sk._SENTENCE_SPLIT_RE, sk._DATE_PREFIX_RE,
        cc._CLAIM_RE, cc._PRONOUN_RE, cc._BETWEEN_PARTY_RE, cc._NEGATION_RE,
        cc._NON_OPERATING_REVENUE_RE, cc._CLAUSE_CUT_RE, cc._BACKING_RE, cc._DIGIT_RE,
        cc._FORWARD_RE, cc._INTEREST_ONLY_RE, subject_re,
        *st._INTEREST_ONLY_RES, st._REVENUE_WORD_RE,
        *(p for _s, p in st._self_patterns("Lindian Resources")), st._COMMERCIAL_STARTED_RE,
        st._COMMERCIAL_STAGE_VETO_RE, *(p for _n, _side, p in st._CUSTOMER_FUNDS_RES),
        st._ROW_FIGURE_RE, cc._NON_PRODUCT_OBJECT_RE, sk._ENDED_PERIOD_WORDS_RE,
        sk._ESTIMATE_SUFFIX_RE, inv_mod._RELAY_RE,
        er._SEC_COVER_RE, er._FORM_TYPE_RE, er._FILING_ANCHOR_RE, er._HEADER_CIK_RE,
        ap._NET_DEBT_SENTENCE_RE,
    ]


class TestLinearPatterns:
    @pytest.mark.parametrize("text", ADVERSARIAL, ids=["spaces", "newlines", "A-", "a,", "a."])
    def test_every_new_pattern_is_linear(self, text: str) -> None:
        for pattern in _patterns():
            started = time.perf_counter()
            list(pattern.finditer(text))
            assert time.perf_counter() - started < 0.2, pattern.pattern[:60]

    @pytest.mark.parametrize("text", ADVERSARIAL, ids=["spaces", "newlines", "A-", "a,", "a."])
    def test_every_new_function_is_bounded(self, text: str) -> None:
        started = time.perf_counter()
        sk.forward_kind(text, AS_OF, ADOBE)
        sk.parse_declared_period(text)
        cc.claims_commercial_operation(text, LINDIAN)
        cc.backs_commercial_operation({"statement": text, "source_kinds": ["issuer_filing"]})
        st.revenue_is_interest_only([text])
        st.assess_commercial_stage([text], issuer_name="Lindian Resources Limited",
                                   revenue_absent=True)
        st.holds_customer_funds([text])
        sk.decide(declared_kind="guidance", declared_period="FY2024", statement=text,
                  published_at=None, as_of=AS_OF)
        from app.services.agents.investigator import statement_opens_with_subject

        statement_opens_with_subject(text, "Lindian Resources Limited", "LIN")
        st.customer_funds_terms([text])
        ap.net_debt_sentence_blocked(text, {"net_debt_not_meaningful": True})
        assert time.perf_counter() - started < 1.0
