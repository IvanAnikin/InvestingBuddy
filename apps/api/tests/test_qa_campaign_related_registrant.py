"""A separately registered company carrying the issuer's name is not the issuer (ADR-066).

Cross-sector QA, 2026-11-01: Honeywell's report carried "Honeywell Aerospace backlog was
$18.2 billion", "Honeywell Aerospace announced a $500M … Tempe facility" and "Honeywell
Aerospace paid over $2M to settle False Claims Act allegations" as Honeywell findings.
Honeywell Aerospace Inc. (HONA) files with the SEC under its own CIK. Its releases are
published on honeywell.com, so the publisher firewall (keyed on the domain) and the
registered-companies guard (which skips any name beginning with the subject's word)
both let them through.

The rule: a statement resting only on web text that names a name-sharing registrant and
never names the subject on its own is that registrant's. The regulator's ticker list
says which names are separate registrants. Nothing here is issuer-specific: the fixture
names are made up apart from the HON/HONA pair the defect was found on.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from app.services.agents import investigator as inv_mod
from app.services.agents.investigator import (
    LLMInvestigator,
    RelatedRegistrant,
    _Evidence,
    related_registrants,
    statement_about_related_registrant,
)
from app.services.director.planner import PlannedQuestion
from app.services.discovery import directories

_REAL_SEC_ANNUAL_ACCESSIONS = directories.sec_annual_accessions


@dataclass
class _Listing:
    name: str
    ticker: str
    cik: str | None


DIRECTORY = [
    _Listing("HONEYWELL INTERNATIONAL INC", "HON", "773840"),
    _Listing("Honeywell Aerospace Inc.", "HONA", "2089271"),
    _Listing("Cullen/Frost Bankers, Inc.", "CFR", "39263"),
    _Listing("Vexmoor Holdings Inc", "VXM", "1001"),
    _Listing("Vexmoor Holdings Inc", "VXM-PA", "1001"),  # preferred, same CIK
    _Listing("Vexmoor Energy Partners LP", "VXEP", "1002"),  # generic second word
    _Listing("Vexmoor Robotics Corp", "VXR", "1003"),
    _Listing("First Solar, Inc.", "FSLR", "1274494"),
    _Listing("First Horizon Corp", "FHN", "36966"),
    # Legal-suffix word in second place: the phrase must be the name as written.
    _Listing("Vexmoor Company Gas", "VXG", "1004"),
    _Listing("WESTERN DIGITAL CORP", "WDC", "106040"),
    _Listing("Western Digital Holdings Trust", "WDHT", "1005"),
]

SEPARATE: dict[str, frozenset[str] | None] = {
    "2089271": frozenset(), "773840": frozenset({"0000773840-26-000010"}),
    "1003": frozenset({"0000001003-26-000001"}),
}
HONA = RelatedRegistrant(
    "honeywell aerospace", "Honeywell Aerospace Inc.", "HONA", "2089271", "773840"
)


class TestRelatedRegistrants:
    def test_a_spun_off_registrant_sharing_the_name_is_found(self) -> None:
        assert related_registrants(DIRECTORY, "HON", "Honeywell International Inc.") == (HONA,)

    def test_the_subjects_own_other_share_classes_are_not_related(self) -> None:
        found = related_registrants(DIRECTORY, "VXM", "Vexmoor Holdings Inc")
        # Not VXM-PA (same CIK), not the generic "Energy", not "Company Gas" (no "vexmoor gas").
        assert [r.ticker for r in found] == ["VXR"]

    def test_the_subjects_own_regulator_name_counts_as_its_own_words(self) -> None:
        # The platform's table holds a short name; the SEC row says "Western Digital".
        assert related_registrants(DIRECTORY, "WDC", "Western Digital") == ()
        assert related_registrants(DIRECTORY, "WDC", "Western") == ()

    def test_a_ticker_reused_on_another_venue_never_stands_in_for_the_subject(self) -> None:
        # Richemont's SIX ticker is CFR; the SEC list's CFR is Cullen/Frost.
        assert related_registrants(DIRECTORY, "CFR", "Compagnie Financiere Richemont") == ()

    def test_a_generic_first_word_names_no_family(self) -> None:
        assert related_registrants(DIRECTORY, "FSLR", "First Solar, Inc.") == ()

    def test_a_subject_the_directory_does_not_hold_gets_nothing(self) -> None:
        assert related_registrants(DIRECTORY, "ZZZZ", "Honeywell International") == ()
        assert related_registrants([], "HON", "Honeywell International") == ()


class TestStatementAboutRelatedRegistrant:
    @pytest.mark.parametrize(
        "statement",
        [
            "Honeywell Aerospace backlog was $18.2 billion at quarter end.",
            "Honeywell Aerospace announced a $500M investment in a Tempe facility.",
            "HONEYWELL AEROSPACE paid over $2M to settle False Claims Act allegations.",
            "In March 2026, Honeywell Aerospace opened a new facility.",
            "The Honeywell Aerospace backlog stood at $18.2 billion.",
            # Review round 1: an appositive after the name, a dated lead-in, odd spacing.
            "Honeywell Aerospace, based in Phoenix, announced a $500M facility in Tempe.",
            "The Honeywell Aerospace unit, now listed, reported backlog of $18.2 billion.",
            "On Oct. 30, 2026, Honeywell Aerospace began trading on Nasdaq.",
            "Honeywell  Aerospace announced a new contract.",
            "Honeywell\nAerospace announced a new contract.",
        ],
    )
    def test_the_registrants_own_news_is_its_own(self, statement: str) -> None:
        assert statement_about_related_registrant(statement, [HONA], "HON") == HONA

    @pytest.mark.parametrize(
        "statement",
        [
            "Honeywell completed the spin-off of Honeywell Aerospace in the quarter.",
            "Honeywell's Aerospace Technologies segment grew sales 9%.",
            "Honeywell reported organic growth of 4%.",
            "Aerospace demand remained strong across the industry.",
            # The registrant as an object: a counterparty of the subject is its fact.
            "The company sold its remaining stake to Honeywell Aerospace.",
            "HON signed a ten-year supply agreement with Honeywell Aerospace.",
            # A long lead-in: the registrant is no longer what the sentence is about.
            "Under a supply agreement signed with the issuer in 2024 and extended twice "
            "since, Honeywell Aerospace buys sensors.",
            # The subject named by its ticker, even in a lead-in.
            "Per HON's 10-Q, Honeywell Aerospace sales were $4B.",
            "Honeywell kept its avionics unit; Honeywell Aerospace took the engines.",
            # Review round 2: the subject implicit in the first clause or sentence. A
            # registrant in a later clause is a miss, never a drop — including the live
            # finding F15 (documented in ADR-066).
            "Organic sales grew 6% in Q3; Honeywell Aerospace led with 12% growth.",
            "Organic sales grew 6% in Q3. Separately, Honeywell Aerospace won a $1B contract.",
            "Key catalysts include the Solstice spin-off completed in 2025; Honeywell "
            "Aerospace separation expected in 2H 2026.",
            "The Tempe plant is tied to US$15 billion in new Control Systems programme "
            "wins; Honeywell Aerospace backlog was $18.2 billion.",
        ],
    )
    def test_a_statement_naming_the_subject_on_its_own_is_kept(self, statement: str) -> None:
        assert statement_about_related_registrant(statement, [HONA], "HON") is None

    def test_a_ticker_inside_a_longer_word_is_not_the_ticker(self) -> None:
        assert statement_about_related_registrant(
            "HONEYWELL AEROSPACE won a HONDA contract.", [HONA], "HON"
        ) == HONA

    def test_no_related_registrants_means_no_verdict(self) -> None:
        assert statement_about_related_registrant("Honeywell Aerospace grew.", []) is None


# ---------------------------------------------------------------------------
# The investigator: the guard runs where the other-company guard runs
# ---------------------------------------------------------------------------


class _Client:
    def __init__(self, statements: list[str]) -> None:
        self._payload = {
            "findings": [
                {"statement": s, "evidence_ids": ["ev:w:abc123"], "direction": "neutral"}
                for s in statements
            ]
        }
        self.last_response_truncated = False

    async def complete_json(self, system: str, user: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        return self._payload


def _question() -> PlannedQuestion:
    return PlannedQuestion(
        key="catalysts",
        text="What upcoming events could change the investment case?",
        origin="playbook",
        required_tools=frozenset({"search_company_corpus"}),
    )


def _evidence(*, web: bool) -> list[_Evidence]:
    return [
        _Evidence(
            citation_id="ev:w:abc123",
            kind="search_web_corpus" if web else "search_company_corpus",
            text='{"evidence_id": "ev:w:abc123", "text": "…"}',
            period_key="FY2026",
            scope_key="group",
            web=web,
        )
    ]


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    statements: list[str],
    *,
    web: bool = True,
    exchange: str | None = "US",
    directory: list[_Listing] | None = None,
    accessions: dict[str, frozenset[str] | None] | None = None,
) -> tuple[LLMInvestigator, list[Any], list[Any]]:
    async def _directory() -> list[_Listing]:
        return DIRECTORY if directory is None else directory

    # Default: HONA has filed no annual report yet; HON files its own 10-K.
    annual = SEPARATE if accessions is None else accessions

    async def _accessions(cik: str | None, **_kw: Any) -> frozenset[str] | None:  # noqa: ANN401
        return annual.get(str(cik))

    monkeypatch.setattr(inv_mod, "_load_sec_directory", _directory)
    monkeypatch.setattr(directories, "sec_annual_accessions", _accessions)
    inv = LLMInvestigator(
        session=object(),
        company_id=uuid.uuid4(),
        ticker="HON",
        exchange=exchange,
        client=_Client(statements),
        company_name="Honeywell International Inc.",
    )
    findings, gaps, _answered = await inv._write_up("event_analyst", _question(), _evidence(web=web))
    return inv, findings, gaps


class TestInvestigatorGuard:
    async def test_web_news_of_the_related_registrant_is_dropped_with_one_gap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inv, findings, gaps = await _run(
            monkeypatch,
            [
                "Honeywell Aerospace announced a $500M investment in a Tempe facility.",
                "Honeywell Aerospace backlog was $18.2 billion.",
                "Honeywell completed the spin-off of Honeywell Aerospace.",
            ],
        )
        assert [f.statement for f in findings] == [
            "Honeywell completed the spin-off of Honeywell Aerospace."
        ]
        assert inv.diagnostics.statements_about_related_registrant == 2
        entity = [g for g in gaps if g.gap_type == "entity_ambiguous"]
        assert len(entity) == 1
        assert "Honeywell Aerospace Inc. (HONA)" in entity[0].description
        assert inv.diagnostics.to_dict()["statements_about_related_registrant"] == 2

    async def test_a_statement_resting_on_the_issuers_filing_is_not_judged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The issuer's own filing speaks for the issuer; the guard is for web text.
        inv, findings, _gaps = await _run(
            monkeypatch, ["Honeywell Aerospace backlog was $18.2 billion."], web=False
        )
        assert len(findings) == 1
        assert inv.diagnostics.statements_about_related_registrant == 0

    async def test_a_non_us_subject_never_reads_the_sec_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inv, findings, _gaps = await _run(
            monkeypatch, ["Honeywell Aerospace backlog was $18.2 billion."], exchange="SW"
        )
        assert len(findings) == 1
        assert inv.diagnostics.statements_about_related_registrant == 0

    async def test_an_unreadable_directory_fails_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _boom() -> list[_Listing]:
            raise RuntimeError("sec.gov unreachable")

        monkeypatch.setattr(inv_mod, "_load_sec_directory", _boom)
        inv = LLMInvestigator(
            session=object(),
            company_id=uuid.uuid4(),
            ticker="HON",
            exchange="US",
            client=_Client(["Honeywell Aerospace backlog was $18.2 billion."]),
            company_name="Honeywell International Inc.",
        )
        findings, _gaps, _a = await inv._write_up("event_analyst", _question(), _evidence(web=True))
        assert len(findings) == 1


class TestOneReportingGroupIsNotSeparate:
    """Review round 1: a consolidated subsidiary with its own registered debt (Entergy
    Louisiana beside Entergy) files a COMBINED 10-K with its parent. Its rate case is the
    parent's fact and must survive."""

    async def test_a_combined_annual_report_keeps_the_statement(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        shared = frozenset({"0000773840-26-000010"})
        inv, findings, gaps = await _run(
            monkeypatch,
            ["Honeywell Aerospace received approval for a $2 billion rate increase."],
            accessions={"2089271": shared, "773840": shared},
        )
        assert len(findings) == 1
        assert inv.diagnostics.statements_about_related_registrant == 0
        assert not [g for g in gaps if g.gap_type == "entity_ambiguous"]

    async def test_an_unreadable_submissions_record_keeps_the_statement(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inv, findings, _gaps = await _run(
            monkeypatch,
            ["Honeywell Aerospace backlog was $18.2 billion."],
            accessions={"2089271": None, "773840": frozenset({"x"})},
        )
        assert len(findings) == 1
        assert inv.diagnostics.statements_about_related_registrant == 0

    async def test_the_gap_names_every_registrant_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        directory = [
            *DIRECTORY,
            _Listing("Honeywell Quantum Inc.", "HONQ", "1006"),
        ]
        inv, findings, gaps = await _run(
            monkeypatch,
            [
                "Honeywell Aerospace backlog was $18.2 billion.",
                "Honeywell Quantum raised $600M.",
            ],
            directory=directory,
            accessions={**SEPARATE, "1006": frozenset()},
        )
        assert findings == []
        entity = [g for g in gaps if g.gap_type == "entity_ambiguous"]
        assert len(entity) == 1
        assert "(HONA)" in entity[0].description and "(HONQ)" in entity[0].description



class TestSecAnnualAccessions:
    """The helper itself, with a fake fetcher. conftest replaces the module attribute,
    so the genuine function is captured before any fixture runs."""

    @staticmethod
    def _fetcher(body: bytes | None, ok: bool = True) -> Any:  # noqa: ANN401
        class _Result:
            def __init__(self) -> None:
                self.ok = ok
                self.content = body

        async def _fetch(url: str, **_kw: Any) -> _Result:  # noqa: ANN401
            assert url == "https://data.sec.gov/submissions/CIK0000065984.json"
            return _Result()

        return _fetch

    async def test_annual_forms_only(self) -> None:
        directories._ANNUAL_ACCESSIONS_CACHE.clear()
        body = (
            b'{"filings": {"recent": {"form": ["10-Q", "10-K", "8-K", "10-K/A"],'
            b' "accessionNumber": ["a-1", "a-2", "a-3", "a-4"]}}}'
        )
        got = await _REAL_SEC_ANNUAL_ACCESSIONS("65984", fetcher=self._fetcher(body))
        assert got == frozenset({"a-2", "a-4"})

    @pytest.mark.parametrize("body,ok", [(b"not json", True), (b"{}", True), (None, False)])
    async def test_an_unreadable_record_is_no_verdict(self, body: bytes | None, ok: bool) -> None:
        directories._ANNUAL_ACCESSIONS_CACHE.clear()
        got = await _REAL_SEC_ANNUAL_ACCESSIONS("65984", fetcher=self._fetcher(body, ok))
        assert got is None

    async def test_a_malformed_cik_never_reaches_the_network(self) -> None:
        assert await _REAL_SEC_ANNUAL_ACCESSIONS("../etc", fetcher=None) is None


class TestReviewRound2:
    async def test_a_subject_with_no_annual_report_of_its_own_drops_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Newly listed, or last report a form the record does not carry: no comparison.
        inv, findings, _gaps = await _run(
            monkeypatch,
            ["Honeywell Aerospace backlog was $18.2 billion."],
            accessions={"2089271": frozenset(), "773840": frozenset()},
        )
        assert len(findings) == 1
        assert inv.diagnostics.statements_about_related_registrant == 0

    async def test_the_regulator_is_asked_once_per_registrant_failures_included(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        async def _directory() -> list[_Listing]:
            return DIRECTORY

        async def _unreadable(cik: str | None, **_kw: Any) -> None:  # noqa: ANN401
            calls.append(str(cik))
            return None

        monkeypatch.setattr(inv_mod, "_load_sec_directory", _directory)
        monkeypatch.setattr(directories, "sec_annual_accessions", _unreadable)
        inv = LLMInvestigator(
            session=object(), company_id=uuid.uuid4(), ticker="HON", exchange="US",
            client=_Client([
                "Honeywell Aerospace backlog was $18.2 billion.",
                "Honeywell Aerospace opened a plant.",
                "Honeywell Aerospace won a contract.",
                "Honeywell Aerospace hired a CFO.",
            ]),
            company_name="Honeywell International Inc.",
        )
        findings, _g, _a = await inv._write_up("event_analyst", _question(), _evidence(web=True))
        assert len(findings) == 4  # fails open
        assert calls == ["773840"]  # one read, remembered

    async def test_a_failed_read_backs_off_instead_of_retrying_per_statement(self) -> None:
        directories.reset_cache()
        attempts: list[str] = []

        async def _refusing(url: str, **_kw: Any) -> Any:  # noqa: ANN401
            attempts.append(url)
            raise TimeoutError("data.sec.gov did not answer")

        assert await _REAL_SEC_ANNUAL_ACCESSIONS("773840", fetcher=_refusing) is None
        assert await _REAL_SEC_ANNUAL_ACCESSIONS("2089271", fetcher=_refusing) is None
        assert len(attempts) == 1
        directories.reset_cache()

    async def test_a_transition_report_is_an_annual_report(self) -> None:
        directories.reset_cache()

        class _R:
            ok = True
            content = (
                b'{"filings": {"recent": {"form": ["10-KT"], "accessionNumber": ["t-1"]}}}'
            )

        async def _fetch(url: str, **_kw: Any) -> _R:  # noqa: ANN401
            return _R()

        assert await _REAL_SEC_ANNUAL_ACCESSIONS("1", fetcher=_fetch) == frozenset({"t-1"})
        directories.reset_cache()

