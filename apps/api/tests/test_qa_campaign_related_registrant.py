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
]

HONA = RelatedRegistrant("honeywell aerospace", "Honeywell Aerospace Inc.", "HONA")


class TestRelatedRegistrants:
    def test_a_spun_off_registrant_sharing_the_name_is_found(self) -> None:
        assert related_registrants(DIRECTORY, "HON", "Honeywell International Inc.") == (HONA,)

    def test_the_subjects_own_other_share_classes_are_not_related(self) -> None:
        found = related_registrants(DIRECTORY, "VXM", "Vexmoor Holdings Inc")
        assert [r.ticker for r in found] == ["VXR"]  # not VXM-PA; not the generic "Energy"

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
        ],
    )
    def test_the_registrants_own_news_is_its_own(self, statement: str) -> None:
        assert statement_about_related_registrant(statement, [HONA]) == HONA

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
        ],
    )
    def test_a_statement_naming_the_subject_on_its_own_is_kept(self, statement: str) -> None:
        assert statement_about_related_registrant(statement, [HONA]) is None

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
) -> tuple[LLMInvestigator, list[Any], list[Any]]:
    async def _directory() -> list[_Listing]:
        return DIRECTORY if directory is None else directory

    monkeypatch.setattr(inv_mod, "_load_sec_directory", _directory)
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
