"""ADR-077 — a 20-F / 40-F filer's interim results arrive on a Form 6-K.

Live Cellectis rerun 686d71f6 (2026-11-02): the 20-F was read (ADR-075) but
``ensure_core_filings`` looked for a 10-Q only, so ``current_period`` said "No interim
report acquired" while the H1 2026 results sat on a 6-K and an iXBRL 6-K/A. These tests
pin:

  * the selector picks the newest 6-K whose OWN documents show results (iXBRL
    statements, a description, a headline, a file name) and never a notice, a clinical
    or partnership release, a dividend notice or a prospectus;
  * nothing qualifying stays ``not_listed`` with a reason a reader can check;
  * a 10-K filer is unchanged;
  * every SEC read is bounded (index pages, heads);
  * the chosen document — not the 6-K's cover page — is what the bridge reads;
  * ``current_period`` recognises the 6-K, with a period only from the document.

No network: index pages, heads and the bridge are fakes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from typing import Any

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.services.corpus import filing_evidence as fe
from app.services.pipeline.issuer_financials import (
    STATE_ACQUIRED_NOT_EXTRACTED,
    STATE_NOT_ACQUIRED,
    build_financial_statements_state,
)
from app.services.sources import sec_6k_results as s6
from app.services.sources.sec_filing_documents import parse_filing_index_page

CIK = "1627281"


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


# ── Fakes ───────────────────────────────────────────────────────────────────── #


@dataclass
class _Event:
    form_type: str
    accession_number: str
    filing_date: str
    report_date: str = ""
    primary: str = ""

    @property
    def related_document_url(self) -> str:
        digits = self.accession_number.replace("-", "")
        return f"https://www.sec.gov/Archives/edgar/data/{CIK}/{digits}/{self.primary}"


def _row(name: str, etype: str, size: int, description: str = "", ixbrl: bool = False) -> dict:
    return {"name": name, "type": etype, "size": str(size), "description": description,
            "ixbrl": ixbrl}


def _pr(body_lines: list[str]) -> bytes:
    """A newswire press release: a block per headline / sub-head line."""
    paras = "".join(f"<p>{line}</p>" for line in body_lines)
    return f"<html><head><title>x</title></head><body>{paras}</body></html>".encode()


#: The statement tables a real results release carries (passes the content check).
_STATEMENTS = [
    "Revenues were $12.4 million for the six months ended June 30, 2026.",
    "Net loss attributable to shareholders was $(41.2) million, or $(0.41) per share.",
    "Operating loss was $(38.9) million compared with $(44.0) million.",
    "Cash and cash equivalents of $169.0 million as of June 30, 2026.",
    "Research and development expenses were $34.1 million.",
]
_PROSE = ("The Company continued to advance its programs during the period and "
          "remains focused on disciplined capital allocation. ")


def _report(body_lines: list[str]) -> bytes:
    """A results release: its headline, then statement figures and ~16 KB of text."""
    return _pr([*body_lines, *_STATEMENTS, _PROSE * 140])


class _Sec:
    """Index pages and document heads by accession, counting every read."""

    def __init__(self, pages: dict[str, list[dict]], heads: dict[str, bytes] | None = None):
        self.pages = {k.replace("-", ""): v for k, v in pages.items()}
        self.heads = heads or {}
        self.page_calls: list[str] = []
        self.head_calls: list[tuple[str, str]] = []

    async def page(self, cik: str, accession: str, **_kw: Any) -> list[dict]:
        self.page_calls.append(accession)
        return self.pages.get(accession, [])

    async def head(self, cik: str, accession: str, name: str, **_kw: Any) -> bytes | None:
        self.head_calls.append((accession, name))
        return self.heads.get(name)


async def _select(events: list[_Event], sec: _Sec, **kw: Any) -> s6.SixKSelection:
    return await s6.select_results_6k(
        events, cik=CIK, page_fetcher=sec.page, head_fetcher=sec.head, **kw
    )


# The Cellectis stream as filed (2026-09 … 2026-10), newest first.
CLLS_EVENTS = [
    _Event("6-K", "0001171843-26-006873", "2026-10-28", "2026-10-27", "f6k_102826.htm"),
    _Event("6-K", "0001171843-26-006010", "2026-09-14", "2026-09-14", "f6k_091326.htm"),
    _Event("6-K", "0001171843-26-006002", "2026-09-11", "2026-09-11", "f6k_091126.htm"),
    _Event("6-K/A", "0001193125-26-389336", "2026-09-11", "2026-06-30", "clls-20260630.htm"),
    _Event("6-K", "0001171843-26-005324", "2026-08-06", "2026-08-06", "f6k_080626.htm"),
    _Event("6-K/A", "0001193125-26-285565", "2026-06-26", "2026-06-26",
           "6-ka_agm_vote_results.htm"),
    _Event("20-F", "0001193125-26-116428", "2026-03-20", "2025-12-31", "clls-20251231.htm"),
]


def _newswire(cover: str, exhibit: str = "exh_991.htm", size: int = 9_000) -> list[dict]:
    return [_row(cover, "6-K", 6_000, "FORM 6-K"),
            _row(exhibit, "EX-99.1", size, "PRESS RELEASE")]


def _clls_sec() -> _Sec:
    return _Sec(
        pages={
            "0001171843-26-006873": _newswire("f6k_102826.htm", "exh_991.htm"),
            "0001171843-26-006010": _newswire("f6k_091326.htm", "exh_pivot.htm"),
            "0001171843-26-006002": _newswire("f6k_091126.htm", "exh_call.htm"),
            "0001193125-26-389336": [
                _row("clls-20260630.htm", "6-K/A", 282_858, "6-K/A", ixbrl=True),
                _row("clls-ex99_1.htm", "EX-99.1", 4_241_281, "EX-99.1", ixbrl=True),
            ],
            "0001171843-26-005324": _newswire("f6k_080626.htm", "exh_993.htm", 93_503),
        },
        heads={
            "exh_991.htm": _pr([
                "EXHIBIT 99.1",
                "Cellectis Presents Preclinical Data Highlighting Efficient APOC3 Editing "
                "with .HEAL-101 at ESGCT 2026",
                "76% triglyceride reduction after a single dose in murine disease model",
            ]),
            "exh_pivot.htm": _pr([
                "EXHIBIT 99.1",
                "Cellectis Announces Strategic Transformation To In Vivo Gene Editing Company",
                "Preliminary Phase 1 data planned in H2 2027 and H1 2028, respectively",
            ]),
            "exh_call.htm": _pr([
                "EXHIBIT 99.1",
                "Cellectis to Host Conference Call and Webcast on September 14 to Provide "
                "a Business Update",
            ]),
            "exh_993.htm": _report([
                "EXHIBIT 99.1",
                "Cellectis Reports Financial Results for the Second Quarter 2026",
                "Pivotal Phase 2 first interim analysis expected in Q4 2026",
            ]),
        },
    )


# ── 1. The rules, pure ──────────────────────────────────────────────────────── #


@pytest.mark.parametrize("text", [
    "Cellectis Reports Financial Results for the Second Quarter 2026",
    "SAP Announces Q3 2026 Results",
    "Shell plc 3rd quarter 2026 unaudited results",
    "Half-year report 2026",
    "Quarterly Statement dated October 21, 2026",
    "Interim report for the six months ended 30 June 2026",
    # Review round 2 (R1): a call, a webcast or a buyback beside a results phrase
    # vetoes nothing.
    "Q3 2026 results and conference call details",
    "Half-year results including share buyback update",
    "Acme Reports Third Quarter 2026 Financial Results; Management to Host Conference "
    "Call Today at 8:30 a.m. ET",
    "Acme Q3 2026 results - webcast at 9am",
    "Q2 2026 results webcast",
    "Acme Reports Q3 2026 Results; Conference Call on November 5",
    "Novo Nordisk: Company announcement - Financial report for the period 1 January 2026 "
    "to 30 June 2026",
])
def test_a_results_title_names_results(text: str) -> None:
    assert s6.text_names_results(text)


@pytest.mark.parametrize("text", [
    "Cellectis Presents Preclinical Data Highlighting APOC3 Editing with .HEAL-101",
    "Positive topline results from the Phase 2 trial expected in the first quarter of 2027",
    "Shell plc Third Quarter 2026 Interim Dividend",
    "Cellectis and AstraZeneca Expand Strategic Partnership",
    "Notice of Annual General Meeting 2026",
    "Total number of voting rights and share capital as of September 30, 2026",
    "Novo Nordisk A/S - Share repurchase programme",
    "Prospectus supplement dated October 1, 2026",
    "Annual financial report 2025",
    # Review of PR #313 (D2): announcements and notices ABOUT results.
    "Cellectis to Report Third Quarter 2026 Financial Results and Business Updates on "
    "November 5, 2026",
    "BioNTech to Report Third Quarter 2026 Financial Results on November 3",
    "Cellectis: Half-Year Report on the Liquidity Contract with Oddo BHF",
    "Availability of the Half-Year Financial Report",
    "Invitation to conference call on Q3 2026 results",
    "Half-year report on share buyback programme",
    "Toyota to announce financial results for the first half of fiscal year ending March 2027",
    "Notice of Q3 2026 earnings release date",
    "Third Quarter 2026 Results on November 5",
    # Review round 3 (R3): future-results announcements.
    "Acme to Host Third Quarter 2026 Financial Results Conference Call and Webcast",
    "Acme to Hold Q3 2026 Earnings Call on 5 November 2026",
    "Acme Announces Date of Third Quarter 2026 Financial Results",
    "Acme Sets Date for Third Quarter 2026 Financial Results",
    "Acme Announces Q3 2026 Results Conference Call on November 5",
    "Acme Schedules Third Quarter 2026 Earnings Release for November 5",
    "Acme will host its Q3 2026 results call",
    # Review round 3 (R4): a full-year title is not the interim slot's.
    "Publication of the 2025 Financial Report",
    "Full Year 2025 Financial Results",
    "Annual Results 2025",
])
def test_a_notice_clinical_or_partnership_title_does_not_name_results(text: str) -> None:
    assert not s6.text_names_results(text)


def test_a_paragraph_is_never_a_title() -> None:
    """SAP's cover: a forward-looking-statements paragraph warns about 'financial
    results'. Only the exhibit index line is a title."""
    boilerplate = ("Any statements contained in this document that are not historical facts "
                   "are forward-looking statements. " * 4
                   + "Our financial results may differ materially.")
    head = s6.headline_of(_pr([boilerplate, "Quarterly Statement dated October 21, 2026"]),
                          window=6000)
    assert s6.results_title(head) == "Quarterly Statement dated October 21, 2026"
    only_boilerplate = s6.headline_of(_pr([boilerplate]), window=6000)
    assert s6.classify_6k(_newswire("tm_6k.htm"), headline=only_boilerplate)[0] == "undecided"


@pytest.mark.parametrize("name", [
    "form6-ktpamtoplineresultsb.htm",   # BioNTech's Phase 3 topline release (D1)
    "trialresults.htm",
    "ex99-1_topline_results.htm",
    "results.htm",
    "earnings.htm",
    "financialstatements.htm",
    "resultsofagm.htm",
    "q2-phase3-interim.htm",
    "h1-liquidity.htm",
    "pr-q3-invitation.htm",
])
def test_a_file_name_without_a_period_or_about_a_trial_is_not_results(name: str) -> None:
    assert not s6.name_names_results(name) or s6.name_names_notice(name)
    assert s6.classify_6k([_row(name, "6-K", 9_000, "6-K")])[0] != "results"


@pytest.mark.parametrize("name", [
    "form6-kq22026quarterlyrepo.htm",   # BioNTech's real Q2 report
    "clls_6-k_q2_2026.htm", "caq22026.htm", "shellq320266-k.htm",
    "form6-kquarterlyfilings.htm", "h1-2026-report.htm", "fy2025results.htm",
    # Review round 2: not a trial, not clinical data, and a call is not a notice.
    "industrialq32026results.htm", "q3-2026-supplementarydata.htm", "conferencecallq3.htm",
])
def test_a_file_name_with_a_period_is_results(name: str) -> None:
    assert s6.name_names_results(name)


async def test_biontech_picks_its_quarterly_report_not_the_topline_release() -> None:
    """Review of PR #313, D1, as filed."""
    events = [
        _Event("6-K", "0001193125-26-000300", "2026-10-26", "2026-10-26",
               "form6-ktpamtoplineresultsb.htm"),
        _Event("6-K", "0001193125-26-000200", "2026-08-04", "2026-08-04",
               "form6-kq22026quarterlyrepo.htm"),
    ]
    sec = _Sec(pages={
        "0001193125-26-000300": [_row("form6-ktpamtoplineresultsb.htm", "6-K", 30_000, "6-K"),
                                 _row("exh991.htm", "EX-99.1", 20_000, "EX-99.1")],
        "0001193125-26-000200": [_row("form6-kq22026quarterlyrepo.htm", "6-K", 900_000, "6-K")],
    }, heads={"exh991.htm": _pr([
        "BioNTech Announces Positive Topline Results from Phase 3 Trial"]),
        "form6-kq22026quarterlyrepo.htm": _report(["Quarterly Report Q2 2026"])})
    sel = await _select(events, sec)
    assert sel.event is events[1]
    assert sel.document_name == "form6-kq22026quarterlyrepo.htm"
    assert sel.examined_log[0]["verdict"] != "results"


async def test_an_announcement_of_results_does_not_beat_the_real_results() -> None:
    """D2, as reproduced: "to Report Third Quarter 2026 Financial Results … on November 5"
    must not become the current period."""
    events = [_Event("6-K", "0001171843-26-006873", "2026-10-28", "", "f6k_102826.htm"),
              _Event("6-K", "0001171843-26-005324", "2026-08-06", "", "f6k_080626.htm")]
    sec = _Sec(pages={
        "0001171843-26-006873": _newswire("f6k_102826.htm", "exh_ann.htm", 15_000),
        "0001171843-26-005324": _newswire("f6k_080626.htm", "exh_993.htm", 90_000),
    }, heads={
        "exh_ann.htm": _pr(["Exhibit 99.1",
                           "Cellectis to Report Third Quarter 2026 Financial Results and "
                           "Business Updates on November 5, 2026",
                           "Cellectis will host a conference call"]),
        "exh_993.htm": _report(["Cellectis Reports Financial Results for the Second Quarter 2026",
                            "Conference call and webcast today at 8:00 a.m. ET"]),
    })
    sel = await _select(events, sec)
    assert sel.event is events[1]
    assert sel.title == "Cellectis Reports Financial Results for the Second Quarter 2026"
    assert [log["verdict"] for log in sel.examined_log] == ["notice", "results"]


async def test_a_6k_filed_before_the_annual_report_is_never_the_current_period() -> None:
    """D3, Barrick-shaped: a Q4 iXBRL 6-K filed 2026-02-05, the 40-F on 2026-02-27."""
    q4 = _Event("6-K", "0001193125-26-000100", "2026-02-05", "2025-12-31", "abx-20251231.htm")
    sec = _Sec(pages={"0001193125-26-000100": [
        _row("abx-20251231.htm", "6-K", 900_000, "6-K", ixbrl=True)]})
    sel = await _select([q4], sec, since="2026-02-27")
    assert sel.event is None and sec.page_calls == []
    assert sel.reason == "no results 6-K since the annual report of 2026-02-27: no 6-K filed after it"
    # Without an annual report to anchor on, the same 6-K is read.
    assert (await _select([q4], _Sec(pages=sec.pages))).event is q4


async def test_an_sec_outage_is_named_in_the_reason() -> None:
    """D4: an unreadable index page is not "examined and found wanting"."""
    events = [_Event("6-K", f"0001171843-26-{9100 + i:06d}", f"2026-10-{20 - i:02d}", "",
                     f"f6k_{i}.htm") for i in range(3)]
    sel = await _select(events, _Sec(pages={}))
    assert sel.index_unreadable == 3
    assert sel.reason == ("no results 6-K among the 3 examined of 3 listed "
                          "(0 skipped as notices by file name; 3 index page(s) unreadable)")


def test_the_content_check_passes_a_results_release() -> None:
    ok, detail = s6.content_shows_results(
        _report(["Cellectis Reports Financial Results for the Second Quarter 2026"]))
    assert ok, detail


@pytest.mark.parametrize("body", [
    # A call notice: no statement figures at all.
    _pr(["Acme to Host Third Quarter 2026 Financial Results Conference Call",
         "The call will take place on November 5, 2026 at 8:30 a.m. ET.", _PROSE * 140]),
    # Long enough, but only two line items with figures.
    _pr(["Acme Q3 2026 results", "Revenues were $12.4 million.",
         "Cash and cash equivalents of $169.0 million.", _PROSE * 140]),
    # Every line item, but a one-page note.
    _pr(["Acme Q3 2026 results", *_STATEMENTS]),
    None,
])
def test_the_content_check_fails_a_document_without_results(body) -> None:
    assert s6.content_shows_results(body)[0] is False


async def test_a_results_named_announcement_fails_the_content_check() -> None:
    """Review round 3: whatever a 6-K is CALLED, a pick must carry results. A newer
    file-name-signalled call notice falls through to the real Q2 release."""
    notice = _Event("6-K", "0000000001-26-000002", "2026-10-28", "", "acme_q3_2026.htm")
    real = _Event("6-K", "0000000001-26-000001", "2026-08-06", "", "acme_q2_2026.htm")
    sec = _Sec(pages={
        "0000000001-26-000002": [_row("acme_q3_2026.htm", "6-K", 12_000, "6-K")],
        "0000000001-26-000001": [_row("acme_q2_2026.htm", "6-K", 400_000, "6-K")],
    }, heads={
        "acme_q3_2026.htm": _pr(["Acme Q3 2026 conference call details", _PROSE * 140]),
        "acme_q2_2026.htm": _report(["Acme Q2 2026"]),
    })
    sel = await _select([notice, real], sec)
    assert sel.event is real
    assert sel.content_failed == 1
    assert sel.examined_log[0]["verdict"] == "content_failed"
    assert sel.content_detail and "statement line items" in sel.content_detail


async def test_r3_announcements_never_beat_the_real_q2() -> None:
    """Review round 3, as probed: each announcement in a newer 6-K, the real Q2 older."""
    for title in [
        "Acme to Host Third Quarter 2026 Financial Results Conference Call and Webcast "
        "on 5 November 2026",
        "Acme Announces Date of Third Quarter 2026 Financial Results",
        "Publication of the 2025 Financial Report",
    ]:
        newer = _Event("6-K", "0000000001-26-000002", "2026-10-28", "", "d1d6k.htm")
        q2 = _Event("6-K", "0000000001-26-000001", "2026-08-06", "", "acme_6-k_q2_2026.htm")
        sec = _Sec(pages={
            "0000000001-26-000002": [_row("d1d6k.htm", "6-K", 20_000, "6-K"),
                                     _row("d1dex991.htm", "EX-99.1", 15_000, "EX-99.1")],
            "0000000001-26-000001": [_row("acme_6-k_q2_2026.htm", "6-K", 20_000, "6-K")],
        }, heads={"d1dex991.htm": _pr(["Exhibit 99.1", title]),
                  "acme_6-k_q2_2026.htm": _report(["Acme Q2 2026 results"])})
        sel = await _select([newer, q2], sec, since="2026-03-01")
        assert sel.event is q2, title


@pytest.mark.parametrize(("period_end", "title", "annual_end", "expected"), [
    ("2025-12-31", None, "2025-12-31", False),     # Barrick-shaped Q4 iXBRL 6-K
    ("2026-06-30", None, "2025-12-31", True),
    (None, "Fourth Quarter and Full Year 2025 Financial Results", "2025-12-31", False),
    (None, "Acme Reports Q2 2026 Results", "2025-12-31", True),
    (None, "Acme Reports Q2 2026 Results", None, True),   # unknown: never a guess
    (None, None, "2025-12-31", True),
])
def test_a_pick_must_report_a_period_after_the_annual_report(
    period_end, title, annual_end, expected
) -> None:
    assert s6._period_after_annual(period_end, title, annual_end) is expected


async def test_a_q4_release_after_the_20f_is_refused_by_its_period() -> None:
    """R4: a Q4/FY release filed after the 20-F reports the year the 20-F covers."""
    q4 = _Event("6-K", "0000000001-26-000003", "2026-03-25", "", "d9d6k.htm")
    sec = _Sec(pages={"0000000001-26-000003": [
        _row("d9d6k.htm", "6-K", 20_000, "6-K"),
        _row("d9dex991.htm", "EX-99.1", 90_000, "EX-99.1")]},
        heads={"d9dex991.htm": _report([
            "Acme Reports Fourth Quarter and Full Year 2025 Financial Results"])})
    sel = await _select([q4], sec, since="2026-03-20", annual_period_end="2025-12-31")
    assert sel.event is None and sel.not_after_annual_period == 1
    assert "1 reported a period not after the annual report's" in (sel.reason or "")


async def test_a_call_named_exhibit_never_vetoes_the_results_release() -> None:
    """Review round 2 (R1): an EX-99.2 named "…earningscallpresentation…" had marked
    the Q3 6-K a notice and the Q2 6-K was picked instead — a stale interim."""
    q3 = _Event("6-K", "0000000001-26-000002", "2026-10-28", "", "d123d6k.htm")
    q2 = _Event("6-K", "0000000001-26-000001", "2026-08-06", "", "acme_6-k_q2_2026.htm")
    sec = _Sec(pages={
        "0000000001-26-000002": [
            _row("d123d6k.htm", "6-K", 20_000, "6-K"),
            _row("d123dex991.htm", "EX-99.1", 90_000, "PRESS RELEASE"),
            _row("d123dex992_earningscallpresentation.htm", "EX-99.2", 90_000, "PRESENTATION"),
        ],
        "0000000001-26-000001": [_row("acme_6-k_q2_2026.htm", "6-K", 20_000, "6-K")],
    }, heads={"d123dex991.htm": _report([
        "Exhibit 99.1",
        "Acme Reports Third Quarter 2026 Financial Results; Management to Host Conference "
        "Call Today at 8:30 a.m. ET"])})
    sel = await _select([q3, q2], sec, since="2026-03-01")
    assert sel.event is q3 and sel.document_name == "d123dex991.htm"
    assert sel.basis == s6.BASIS_HEADLINE


async def test_an_unreadable_index_page_is_skipped_not_taken_on_its_file_name() -> None:
    """Review round 2 (R2): on an outage Shell's 12 KB cover was taken by its name."""
    async def boom(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("SEC down")

    q3 = _Event("6-K", "0001628280-26-068044", "2026-10-29", "2026-09-30",
                "shellq320266-k.htm")
    sel = await s6.select_results_6k([q3], cik=CIK, page_fetcher=boom, head_fetcher=boom)
    assert sel.event is None and sel.index_unreadable == 1
    assert sel.examined_log[0]["verdict"] == "index_unreadable"
    assert "1 index page(s) unreadable" in (sel.reason or "")


def test_ixbrl_statements_make_a_6k_a_results_release() -> None:
    entries = [_row("x-20260630.htm", "6-K/A", 282_858, "6-K/A", ixbrl=True),
               _row("x-ex99_1.htm", "EX-99.1", 4_241_281, "EX-99.1", ixbrl=True)]
    assert s6.classify_6k(entries) == ("results", s6.BASIS_IXBRL)
    assert s6.results_document(entries, s6.BASIS_IXBRL)["name"] == "x-ex99_1.htm"


def test_a_notice_by_file_name_wins_even_over_the_word_results() -> None:
    entries = [_row("6-ka_agm_vote_results.htm", "6-K/A", 411_465, "6-K/A")]
    assert s6.classify_6k(entries)[0] == "notice"


def test_the_results_document_is_the_substantive_exhibit_not_a_stub() -> None:
    """Shell's Q3 2026 6-K: a 3 KB EX-99.1 beside the 1.8 MB quarterly report."""
    entries = [_row("shellq320266-k.htm", "6-K", 12_365, "6-K"),
               _row("q32026exhibit991.htm", "EX-99.1", 3_041, "EX-99.1"),
               _row("quarterlyexhibit992-q32026.htm", "EX-99.2", 1_790_633, "EX-99.2")]
    assert s6.results_document(entries, s6.BASIS_FILE_NAME)["name"] == (
        "quarterlyexhibit992-q32026.htm")
    asml = [_row("form6-kquarterlyfilings.htm", "6-K", 11_161, "6-K"),
            _row("pressreleasefinancialresul.htm", "EX-99.1", 41_553, "EX-99.1"),
            _row("financial_statementsxusx.htm", "EX-99.3", 28_110, "EX-99.3")]
    assert s6.results_document(asml, s6.BASIS_FILE_NAME)["name"] == (
        "pressreleasefinancialresul.htm")


def test_the_index_page_marks_ixbrl_documents() -> None:
    page = (
        '<table class="tableFile" summary="Document Format Files">'
        "<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>"
        '<tr><td scope="row">1</td><td scope="row">6-K/A</td><td scope="row">'
        '<a href="/ix?doc=/Archives/edgar/data/1627281/000119312526389336/clls-20260630.htm">'
        'clls-20260630.htm</a> &nbsp;<span style="color: green">iXBRL</span></td>'
        '<td scope="row">6-K/A</td><td scope="row">282858</td></tr>'
        '<tr><td scope="row">2</td><td scope="row">PRESS RELEASE</td><td scope="row">'
        '<a href="/Archives/edgar/data/1627281/000117184326005324/exh_991.htm">exh_991.htm</a>'
        '</td><td scope="row">EX-99.1</td><td scope="row">93503</td></tr></table>'
    )
    rows = parse_filing_index_page(page)
    assert [(r["name"], r["ixbrl"]) for r in rows] == [
        ("clls-20260630.htm", True), ("exh_991.htm", False)]


@pytest.mark.parametrize(("report", "filed", "expected"), [
    ("2026-06-30", "2026-09-11", "2026-06-30"),   # iXBRL statements: the balance-sheet date
    ("2026-09-27", "2026-10-14", "2026-09-27"),   # ASML's 52-week quarter end
    ("2026-10-27", "2026-10-28", None),           # a press release's own date
    ("2023-10-12", "2026-10-13", None),           # a garbage date is no period end
    ("", "2026-10-13", None),
])
def test_period_end_is_the_declared_date_or_nothing(report, filed, expected) -> None:
    assert s6.period_end_of(_Event("6-K", "0001-26-1", filed, report)) == expected


# ── 2. Selection over a filer's 6-K stream ──────────────────────────────────── #


async def test_a_20f_filer_with_mixed_6ks_picks_the_results_one() -> None:
    sec = _clls_sec()
    sel = await _select(CLLS_EVENTS, sec)
    assert sel.event is not None
    assert (sel.event.form_type, sel.event.filing_date) == ("6-K/A", "2026-09-11")
    assert sel.document_name == "clls-ex99_1.htm"
    assert sel.basis == s6.BASIS_IXBRL
    assert sel.period_end == "2026-06-30"
    # The data and pivot releases were read and refused; the conference-call
    # announcement is a notice.
    assert [log["verdict"] for log in sel.examined_log] == [
        "undecided", "undecided", "notice", "results"]


async def test_a_newer_results_press_release_wins_over_an_older_one() -> None:
    sec = _clls_sec()
    events = [e for e in CLLS_EVENTS if e.form_type != "6-K/A"]
    sel = await _select(events, sec)
    assert sel.event.accession_number == "0001171843-26-005324"
    assert sel.document_name == "exh_993.htm"
    assert sel.basis == s6.BASIS_HEADLINE
    assert sel.title == "Cellectis Reports Financial Results for the Second Quarter 2026"
    assert sel.period_end is None  # a press release's period of report is its own date


async def test_a_results_release_past_the_unsignalled_cap_is_not_examined() -> None:
    """The bound, stated: with no feed signal, only the four newest 6-Ks are read."""
    sec = _clls_sec()
    noise = [_Event("6-K", f"0001171843-26-{7000 + i:06d}", f"2026-10-{30 - i:02d}", "",
                    f"f6k_n{i}.htm") for i in range(4)]
    events = noise + [e for e in CLLS_EVENTS if e.form_type != "6-K/A"]
    sel = await _select(events, sec)
    assert sel.event is None
    assert sel.examined == s6.MAX_UNSIGNALLED_VISITS


async def test_only_non_results_6ks_leave_the_slot_unfilled_with_a_reason() -> None:
    sec = _clls_sec()
    events = CLLS_EVENTS[:3] + [CLLS_EVENTS[5]]
    sel = await _select(events, sec)
    assert sel.event is None
    assert sel.reason == (
        "no results 6-K among the 3 examined of 4 listed (1 skipped as notices by file name)")


async def test_a_notice_by_feed_file_name_costs_no_fetch() -> None:
    sec = _clls_sec()
    await _select([CLLS_EVENTS[5]], sec)
    assert sec.page_calls == [] and sec.head_calls == []


async def test_the_index_fetch_cap_is_respected() -> None:
    """Twelve 6-Ks that the feed flags (a period-end report date) and that say nothing:
    at most six index pages are read."""
    events = [
        _Event("6-K", f"0001171843-26-{9000 + i:06d}", f"2026-10-{28 - i:02d}", "2026-06-30",
               f"f6k_{i}.htm")
        for i in range(12)
    ]
    sec = _Sec(pages={})
    sel = await _select(events, sec)
    assert sel.event is None
    assert len(sec.page_calls) == s6.MAX_INDEX_FETCHES == sel.index_fetches == 6


async def test_unsignalled_6ks_are_capped_so_a_weekly_buyback_filer_reaches_its_report() -> None:
    """Novo Nordisk: weekly buyback 6-Ks, then the interim report (``caq22026.htm``)."""
    buybacks = [
        _Event("6-K", f"0001171843-26-{8000 + i:06d}", f"2026-10-{26 - i:02d}", "",
               f"f6k_{i}.htm")
        for i in range(10)
    ]
    report = _Event("6-K", "0000353278-26-000023", "2026-08-04", "2026-06-30", "caq22026.htm")
    sec = _Sec(pages={"0000353278-26-000023": [_row("caq22026.htm", "6-K", 1_730_052, "6-K")]},
               heads={"caq22026.htm": _report(["Financial report for the period 1 January "
                                               "2026 to 30 June 2026"])})
    sel = await _select([*buybacks, report], sec)
    assert sel.event is report and sel.document_name == "caq22026.htm"
    assert sel.basis == s6.BASIS_FILE_NAME
    assert len(sec.page_calls) == s6.MAX_UNSIGNALLED_VISITS + 1


async def test_a_large_exhibit_is_never_fetched_for_its_headline() -> None:
    events = [_Event("6-K", "0001171843-26-000001", "2026-10-26", "", "f6k_a.htm")]
    sec = _Sec(pages={"0001171843-26-000001": [
        _row("f6k_a.htm", "6-K", 900_000, "FORM 6-K"),
        _row("exh_big.htm", "EX-99.1", 700_000, "EXHIBIT 99.1")]})
    sel = await _select(events, sec)
    assert sel.event is None and sec.head_calls == []


# ── 3. ensure_core_filings ──────────────────────────────────────────────────── #


class _Provider:
    def __init__(self, events: list[_Event]) -> None:
        self.events = events

    async def get_recent_events(self, ticker: str, **kwargs: Any) -> Any:
        return NS(cik=kwargs.get("cik") or CIK, events=self.events, warnings=[])


def _cfg(**over: Any) -> Any:
    base = {"v3_filings_tool_enabled": True, "v3_filing_body_bridge_enabled": True,
            "primary_document_sec_body_enabled": True}
    base.update(over)
    return NS(**base)


@pytest.fixture
def bridged(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def _ensure(session: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return fe.FilingEvidenceResult(state="ready", accession=kwargs["accession"],
                                       chunk_count=12, fetched=True, attempted=True)

    monkeypatch.setattr(fe, "ensure_filing_corpus_evidence", _ensure)
    return calls


def _company() -> Any:
    return NS(id=uuid.uuid4(), ticker="CLLS", exchange="NASDAQ")


async def test_the_quarterly_slot_of_a_20f_filer_reads_the_results_6k(bridged) -> None:
    sec = _clls_sec()

    async def selector(events: list[Any], **kw: Any) -> s6.SixKSelection:
        return await s6.select_results_6k(
            events, page_fetcher=sec.page, head_fetcher=sec.head, **kw)

    out = await fe.ensure_core_filings(None, company=_company(), cfg=_cfg(),
                                       provider=_Provider(CLLS_EVENTS), sixk_selector=selector)
    assert out["annual"]["form"] == "20-F"
    quarterly = out["quarterly"]
    assert quarterly["state"] == "ready"
    assert (quarterly["form"], quarterly["filing_date"]) == ("6-K/A", "2026-09-11")
    assert quarterly["document"] == "clls-ex99_1.htm"
    assert quarterly["basis"] == s6.BASIS_IXBRL
    assert quarterly["period_end"] == "2026-06-30"
    assert quarterly["selection"]["index_fetches"] == 4
    # The bridge was asked for THE exhibit, not the 6-K's cover page.
    assert bridged[1]["document_name"] == "clls-ex99_1.htm"
    assert bridged[1]["accession"] == "0001193125-26-389336"


async def test_no_results_6k_is_not_listed_with_the_reason(bridged) -> None:
    sec = _Sec(pages={})

    async def selector(events: list[Any], **kw: Any) -> s6.SixKSelection:
        return await s6.select_results_6k(
            events, page_fetcher=sec.page, head_fetcher=sec.head, **kw)

    out = await fe.ensure_core_filings(None, company=_company(), cfg=_cfg(),
                                       provider=_Provider(CLLS_EVENTS), sixk_selector=selector)
    assert out["quarterly"]["state"] == "not_listed"
    assert out["quarterly"]["reason"] == (
        "no results 6-K since the annual report of 2026-03-20 among the 5 examined of 6 "
        "listed (1 skipped as notices by file name; 5 index page(s) unreadable)")
    assert len(bridged) == 1  # the 20-F only


async def test_a_10k_filer_is_unchanged(bridged) -> None:
    async def selector(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("a 10-K filer never reads 6-Ks")

    events = [_Event("8-K", "0000000001-26-000003", "2026-10-30"),
              _Event("10-Q", "0000000001-26-000002", "2026-08-01"),
              _Event("10-K", "0000000001-26-000001", "2026-02-20")]
    out = await fe.ensure_core_filings(
        None, company=NS(id=uuid.uuid4(), ticker="SCCO", exchange="NYSE"), cfg=_cfg(),
        provider=_Provider(events), sixk_selector=selector)
    assert [c["form"] for c in bridged] == ["10-K", "10-Q"]
    assert "document_name" not in bridged[1]
    assert out["quarterly"]["form"] == "10-Q"


async def test_a_10k_filer_without_a_10q_stays_not_listed_as_before(bridged) -> None:
    async def selector(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("a 10-K filer never reads 6-Ks")

    events = [_Event("10-K", "0000000001-26-000001", "2026-02-20")]
    out = await fe.ensure_core_filings(
        None, company=NS(id=uuid.uuid4(), ticker="SCCO", exchange="NYSE"), cfg=_cfg(),
        provider=_Provider(events), sixk_selector=selector)
    assert out["quarterly"] == {"state": "not_listed", "forms": ["10-Q"]}


async def test_with_body_fetch_off_no_index_page_is_read(bridged) -> None:
    async def selector(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("no body would be fetched, so nothing is selected")

    out = await fe.ensure_core_filings(
        None, company=_company(), cfg=_cfg(primary_document_sec_body_enabled=False),
        provider=_Provider(CLLS_EVENTS), sixk_selector=selector)
    assert out["quarterly"]["state"] == "not_listed"
    assert out["quarterly"]["reason"] == "SEC filing-body fetch is disabled"


# ── 4. The bridge reads THE named document ──────────────────────────────────── #


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", future=True, poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _bridge_cfg() -> Settings:
    return Settings(  # type: ignore[call-arg]
        v3_corpus_enabled=True, primary_document_ingestion_enabled=True,
        primary_document_sec_body_enabled=True, v3_filings_tool_enabled=True,
        v3_filing_body_bridge_enabled=True, azure_openai_api_key="",
        azure_openai_endpoint="", deepseek_api_key="",
    )


ACC = "0001171843-26-005324"
BASE = f"https://www.sec.gov/Archives/edgar/data/{CIK}/{ACC.replace('-', '')}"


async def _searchable(session: Any, company_id: uuid.UUID, url: str, n: int) -> None:
    from app.models.research_chunk import ResearchDocumentChunk
    from app.models.research_derivation import ResearchDocumentDerivation
    from app.models.research_document import ResearchDocument, ResearchDocumentVersion

    doc = ResearchDocument(id=uuid.uuid4(), company_id=company_id,
                           document_key=f"sec_filing:{ACC}:{n}", document_type="sec_filing",
                           title=f"6-K {ACC}")
    session.add(doc)
    await session.flush()
    version = ResearchDocumentVersion(
        id=uuid.uuid4(), research_document_id=doc.id, content_hash=str(n) * 64,
        canonical_url=url, transport="sec_edgar", source_tier="T1_primary_filing",
        access_class="public_official", extraction_status="extracted",
        retrieved_at=datetime.now(timezone.utc), is_current=True)
    session.add(version)
    await session.flush()
    deriv = ResearchDocumentDerivation(
        id=uuid.uuid4(), research_document_version_id=version.id, pipeline_version=15,
        extraction_profile="live", extraction_method="html", status="complete",
        is_active=True, pages_persisted=1, page_count=1, char_count=100)
    session.add(deriv)
    await session.flush()
    session.add(ResearchDocumentChunk(
        id=uuid.uuid4(), chunk_id=f"c:{ACC}:{n}", derivation_id=deriv.id,
        research_document_version_id=version.id, company_id=company_id, ordinal=1,
        kind="prose", text="Cellectis reports financial results.", char_start=0, char_end=36,
        indexable=True, indexed_at=datetime.now(timezone.utc),
        source_tier="T1_primary_filing", access_class="public_official"))
    await session.flush()


async def _company_row(session: Any) -> Any:
    from app.models.company import Company

    company = Company(id=uuid.uuid4(), ticker="CLLS", exchange="NASDAQ", name="Cellectis",
                      status="new")
    session.add(company)
    await session.flush()
    return company


async def test_a_held_cover_page_does_not_answer_for_the_named_press_release(session) -> None:
    company = await _company_row(session)
    await _searchable(session, company.id, f"{BASE}/f6k_080626.htm", 1)
    calls: list[dict[str, Any]] = []

    async def _acquire(_session: Any, **kwargs: Any) -> fe.AcquireOutcome:
        calls.append(kwargs)
        await _searchable(session, company.id, f"{BASE}/{kwargs['document_name']}", 2)
        return fe.AcquireOutcome(acquired=True, fetched=True)

    result = await fe.ensure_filing_corpus_evidence(
        session, company_id=company.id, cik=CIK, accession=ACC, form="6-K",
        cfg=_bridge_cfg(), acquire=_acquire, document_name="exh_991.htm")
    assert calls and calls[0]["document_name"] == "exh_991.htm"
    assert result.state == "ready" and result.fetched is True

    again = await fe.ensure_filing_corpus_evidence(
        session, company_id=company.id, cik=CIK, accession=ACC, form="6-K",
        cfg=_bridge_cfg(), acquire=_acquire, document_name="exh_991.htm")
    assert again.reused is True and len(calls) == 1


async def test_a_named_exhibit_is_ready_even_with_an_exhibit_shaped_name(session) -> None:
    company = await _company_row(session)
    await _searchable(session, company.id, f"{BASE}/ex99-1.htm", 1)
    state = await fe.filing_evidence_state(
        session, company_id=company.id, accession=ACC, document_name="ex99-1.htm")
    assert state.is_ready
    # Unnamed, the same exhibit is still "not the filing" (unchanged behaviour).
    unnamed = await fe.filing_evidence_state(session, company_id=company.id, accession=ACC)
    assert not unnamed.is_ready


async def test_an_unsafe_document_name_is_refused(session) -> None:
    company = await _company_row(session)
    state = await fe.filing_evidence_state(
        session, company_id=company.id, accession=ACC, document_name="../x.htm")
    assert state.state == "absent"


# ── 5. current_period ───────────────────────────────────────────────────────── #


def _current(quarterly: dict[str, Any]) -> dict[str, Any]:
    return build_financial_statements_state([], core_filings={"quarterly": quarterly})[
        "current_period"]


def test_current_period_recognises_a_results_6k_by_its_headline() -> None:
    cp = _current({"state": "ready", "form": "6-K", "filing_date": "2026-08-06",
                   "title": "Cellectis Reports Financial Results for the Second Quarter 2026"})
    assert cp["state"] == STATE_ACQUIRED_NOT_EXTRACTED
    assert cp["period"] == "Q2 2026"
    assert cp["label"].startswith("Q2 2026 quarterly report acquired (2026-08-06)")


def test_current_period_states_the_declared_period_end_and_invents_nothing() -> None:
    cp = _current({"state": "ready", "form": "6-K/A", "filing_date": "2026-09-11",
                   "title": None, "period_end": "2026-06-30"})
    assert cp["period"] is None
    assert cp["label"].startswith(
        "Interim report for the period ended 2026-06-30 acquired (2026-09-11)")


def test_a_lone_year_in_a_6k_title_is_not_a_fiscal_year() -> None:
    cp = _current({"state": "ready", "form": "6-K", "filing_date": "2026-10-26",
                   "title": "Quarterly Statement dated October 21, 2026"})
    assert cp["period"] is None
    assert cp["label"].startswith("Quarterly report acquired (2026-10-26)")


def test_a_6k_slot_older_than_the_annual_report_is_not_the_current_period() -> None:
    cp = build_financial_statements_state([], core_filings={
        "annual": {"state": "ready", "form": "40-F", "filing_date": "2026-02-27"},
        "quarterly": {"state": "ready", "form": "6-K", "filing_date": "2026-02-05",
                      "title": None, "period_end": "2025-12-31"},
    })["current_period"]
    assert cp["state"] == STATE_NOT_ACQUIRED


def test_an_unread_6k_is_still_no_interim_report() -> None:
    cp = _current({"state": "not_listed", "forms": ["6-K", "6-K/A"],
                   "reason": "no results 6-K among the 6 examined of 30 listed"})
    assert cp["state"] == STATE_NOT_ACQUIRED
    assert cp["label"] == "No interim report acquired"
