"""ADR-068 — an 8-K is a results release only when its EDGAR items include 2.02.

Cross-sector QA campaign 2026-11, defect JPM-2. The SEC filing-body path visited
8-Ks newest first and labelled EVERY 8-K a ``results_release``. A note-offering
legal-opinion 8-K (Items 8.01, 9.01) filed a week after the Item 2.02 earnings 8-K
therefore displaced the quarter's results, and the quarter vanished from the report.

Generic fixtures only. No network: index fetches and body fetches are injected.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.integrations.providers.sec_recent_filings_provider import parse_recent_filings
from app.services.sources import live_fetchers
from app.services.sources.company_evidence import sec_filings_from_catalyst
from app.services.sources.document_discovery import (
    DOC_KIND_INTERIM_REPORT,
    DOC_KIND_OTHER,
    DOC_KIND_RESULTS_RELEASE,
)
from app.services.sources.ingestion_status import FAILURE_NO_PRIMARY_FILING_DOCUMENT
from app.services.sources.sec_filing_documents import (
    SecFilingDocument,
    SecPreflightFailure,
    SecRateLimiter,
    _sorted_filings,
    doc_kind_for_form,
    filing_item_codes,
    is_results_release_8k,
    parse_item_codes,
    resolve_filing_documents,
)

CIK = "0000123456"
ACC_RESULTS = "0000123456-26-000100"  # older, Item 2.02 earnings 8-K
ACC_NOTES = "0000987654-26-000200"  # newer, Items 8.01/9.01 note-offering 8-K
ACC_DECK = "0000123456-26-000101"  # same day as results, Item 7.01 slide deck
ACC_10Q = "0000123456-26-000050"


def _filing(form: str, accession: str, filed: str, items: Any = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "form_type": form,
        "title": f"SEC {form} filing — TEST — {filed}",
        "url": None,
        "filed_date": filed,
        "summary": None,
        "accession_number": accession,
    }
    if items is not None:
        out["item_numbers"] = items
    return out


def _quiet_limiter() -> SecRateLimiter:
    async def _sleep(delay: float) -> None:
        return None

    return SecRateLimiter(min_interval_seconds=0.0, sleep=_sleep, clock=lambda: 0.0)


def _index_fetcher(seen: list[str]):
    async def _fetch(cik: str, accession: str, **kw: Any) -> list[dict[str, Any]]:
        seen.append(accession)
        return [{"name": f"body-{accession}.htm", "type": "8-K", "size": "9000"}]

    return _fetch


def _resolve(filings: list[dict[str, Any]], *, max_documents: int = 1):
    seen: list[str] = []
    docs = asyncio.run(
        resolve_filing_documents(
            CIK,
            filings,
            max_documents=max_documents,
            cfg=SimpleNamespace(),
            limiter=_quiet_limiter(),
            index_fetcher=_index_fetcher(seen),
        )
    )
    return docs, seen


# --------------------------------------------------------------------------- #
# Item-code parsing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2.02,9.01", ("2.02", "9.01")),
        ("2.02, 9.01", ("2.02", "9.01")),
        ("Item 2.02, Item 9.01", ("2.02", "9.01")),
        (["2.02", "9.01"], ("2.02", "9.01")),
        ("1.01,2.01,2.02,3.03,5.02", ("1.01", "2.01", "2.02", "3.03", "5.02")),
        ("", ()),
        (None, ()),
        (202, ()),
        ([], ()),
    ],
)
def test_parse_item_codes_accepts_edgar_and_catalyst_shapes(raw, expected):
    assert parse_item_codes(raw) == expected


def test_filing_item_codes_reads_either_key_and_treats_absence_as_unknown():
    assert filing_item_codes({"item_numbers": ["2.02", "9.01"]}) == ("2.02", "9.01")
    assert filing_item_codes({"items": "2.02,9.01"}) == ("2.02", "9.01")
    assert filing_item_codes({"form_type": "8-K"}) == ()
    assert filing_item_codes(None) == ()


@pytest.mark.parametrize(
    ("form", "items", "expected"),
    [
        ("8-K", "2.02,9.01", True),
        ("8-K", ["9.01", "2.02"], True),
        ("8-K/A", "2.02", True),
        ("8-K", "8.01,9.01", False),
        ("8-K", "7.01,9.01", False),
        ("8-K", None, False),
        ("8-K", "", False),
        ("6-K", "2.02", False),
        ("10-Q", "2.02", False),
    ],
)
def test_is_results_release_8k(form, items, expected):
    assert is_results_release_8k(form, items) is expected


# --------------------------------------------------------------------------- #
# Labelling
# --------------------------------------------------------------------------- #


def test_an_8k_without_item_202_is_labelled_an_8k_not_a_results_release():
    assert doc_kind_for_form("8-K", ("8.01", "9.01")) == DOC_KIND_OTHER
    assert doc_kind_for_form("8-K", "5.02") == DOC_KIND_OTHER


def test_an_item_202_8k_with_several_codes_is_a_results_release():
    assert doc_kind_for_form("8-K", "2.02,9.01") == DOC_KIND_RESULTS_RELEASE
    assert doc_kind_for_form("8-K", ("2.02", "5.02", "9.01")) == DOC_KIND_RESULTS_RELEASE


def test_an_8k_whose_items_are_missing_keeps_the_legacy_label():
    # A caller that passes no item codes cannot be told apart from the old world;
    # the label it always got is unchanged rather than guessed in either direction.
    assert doc_kind_for_form("8-K") == DOC_KIND_RESULTS_RELEASE
    assert doc_kind_for_form("8-K", None) == DOC_KIND_RESULTS_RELEASE
    assert doc_kind_for_form("8-K", ()) == DOC_KIND_RESULTS_RELEASE


def test_6k_label_is_unchanged_whatever_the_items_field_says():
    assert doc_kind_for_form("6-K") == DOC_KIND_INTERIM_REPORT
    assert doc_kind_for_form("6-K", "") == DOC_KIND_INTERIM_REPORT
    assert doc_kind_for_form("6-K/A", ("8.01",)) == DOC_KIND_INTERIM_REPORT


# --------------------------------------------------------------------------- #
# Selection order
# --------------------------------------------------------------------------- #


def test_newer_non_202_8k_never_displaces_the_item_202_8k():
    ordered = _sorted_filings(
        [
            _filing("8-K", ACC_NOTES, "2026-10-20", ["8.01", "9.01"]),
            _filing("8-K", ACC_DECK, "2026-10-13", ["7.01", "9.01"]),
            _filing("8-K", ACC_RESULTS, "2026-10-13", ["2.02", "9.01"]),
        ]
    )
    assert [f["accession_number"] for f in ordered] == [ACC_RESULTS, ACC_NOTES, ACC_DECK]


def test_newest_item_202_8k_wins_among_several_results_releases():
    older = "0000123456-26-000010"
    ordered = _sorted_filings(
        [
            _filing("8-K", older, "2026-07-14", "2.02,9.01"),
            _filing("8-K", ACC_NOTES, "2026-10-20", "8.01,9.01"),
            _filing("8-K", ACC_RESULTS, "2026-10-13", "2.02,9.01"),
        ]
    )
    assert [f["accession_number"] for f in ordered] == [ACC_RESULTS, older, ACC_NOTES]


def test_an_8k_with_missing_items_does_not_outrank_a_known_item_202_8k():
    ordered = _sorted_filings(
        [
            _filing("8-K", ACC_NOTES, "2026-10-20"),  # no items field at all
            _filing("8-K", ACC_RESULTS, "2026-10-13", ["2.02"]),
        ]
    )
    assert ordered[0]["accession_number"] == ACC_RESULTS


def test_with_no_item_codes_anywhere_the_order_is_unchanged_newest_first():
    ordered = _sorted_filings(
        [
            _filing("8-K", ACC_RESULTS, "2026-10-13"),
            _filing("8-K", ACC_NOTES, "2026-10-20"),
            _filing("10-Q", ACC_10Q, "2026-08-06"),
        ]
    )
    assert [f["accession_number"] for f in ordered] == [ACC_10Q, ACC_NOTES, ACC_RESULTS]


def test_form_rank_still_comes_first_a_10q_beats_any_8k():
    ordered = _sorted_filings(
        [
            _filing("8-K", ACC_RESULTS, "2026-10-13", ["2.02"]),
            _filing("10-Q", ACC_10Q, "2026-08-06"),
        ]
    )
    assert ordered[0]["form_type"] == "10-Q"


def test_6k_order_is_unchanged_newest_first():
    filings = [
        _filing("6-K", "0000555555-26-000001", "2026-08-06", ""),
        _filing("6-K", "0000555555-26-000003", "2026-10-28"),
        _filing("6-K/A", "0000555555-26-000002", "2026-09-11", ""),
    ]
    ordered = _sorted_filings(filings)
    assert [f["filed_date"] for f in ordered] == ["2026-10-28", "2026-09-11", "2026-08-06"]


# --------------------------------------------------------------------------- #
# Resolution end to end (index fetch injected — no network)
# --------------------------------------------------------------------------- #


def test_resolution_picks_the_item_202_8k_and_carries_its_items():
    docs, seen = _resolve(
        [
            _filing("8-K", ACC_NOTES, "2026-10-20", ["8.01", "9.01"]),
            _filing("8-K", ACC_RESULTS, "2026-10-13", ["2.02", "9.01"]),
        ]
    )
    assert seen == [ACC_RESULTS.replace("-", "")], "the note-offering 8-K was fetched first"
    assert len(docs) == 1
    assert docs[0].accession_number == ACC_RESULTS
    assert docs[0].item_codes == ("2.02", "9.01")
    assert doc_kind_for_form(docs[0].form_type, docs[0].item_codes) == DOC_KIND_RESULTS_RELEASE


def test_resolution_without_any_item_202_8k_keeps_the_newest_8k_labelled_honestly():
    docs, _ = _resolve(
        [
            _filing("8-K", ACC_DECK, "2026-10-13", ["7.01", "9.01"]),
            _filing("8-K", ACC_NOTES, "2026-10-20", ["8.01", "9.01"]),
        ]
    )
    assert docs[0].accession_number == ACC_NOTES  # existing behaviour: newest 8-K
    assert doc_kind_for_form(docs[0].form_type, docs[0].item_codes) == DOC_KIND_OTHER


def test_resolution_of_6k_filer_is_unchanged():
    docs, seen = _resolve(
        [
            _filing("6-K", "0000555555-26-000001", "2026-08-06"),
            _filing("6-K", "0000555555-26-000003", "2026-10-28"),
        ]
    )
    assert seen == ["000055555526000003"]
    assert docs[0].item_codes == ()
    assert doc_kind_for_form(docs[0].form_type, docs[0].item_codes) == DOC_KIND_INTERIM_REPORT


def test_preflight_failures_carry_item_codes_too():
    sink: list[SecPreflightFailure] = []

    async def _empty(cik: str, accession: str, **kw: Any) -> list[dict[str, Any]]:
        return []

    asyncio.run(
        resolve_filing_documents(
            CIK,
            [_filing("8-K", ACC_NOTES, "2026-10-20", ["8.01", "9.01"])],
            max_documents=1,
            cfg=SimpleNamespace(),
            limiter=_quiet_limiter(),
            index_fetcher=_empty,
            preflight_sink=sink,
        )
    )
    assert [f.failure_code for f in sink] == [FAILURE_NO_PRIMARY_FILING_DOCUMENT]
    assert sink[0].item_codes == ("8.01", "9.01")
    assert live_fetchers._preflight_artifact(sink[0]).doc_kind == DOC_KIND_OTHER


# --------------------------------------------------------------------------- #
# The item codes survive every adapter between EDGAR and the resolver
# --------------------------------------------------------------------------- #


def _submissions(rows: list[tuple[str, str, str, str | None]]) -> dict[str, Any]:
    """A generic EDGAR submissions payload. ``items=None`` drops the field entirely."""
    recent: dict[str, list[Any]] = {
        "form": [r[0] for r in rows],
        "filingDate": [r[1] for r in rows],
        "reportDate": [r[1] for r in rows],
        "accessionNumber": [r[2] for r in rows],
        "primaryDocument": [f"doc{i}.htm" for i in range(len(rows))],
        "primaryDocDescription": ["" for _ in rows],
    }
    if all(r[3] is not None for r in rows):
        recent["items"] = [r[3] for r in rows]
    return {"filings": {"recent": recent}}


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def test_edgar_items_reach_the_resolver_through_the_catalyst_adapters():
    payload = _submissions(
        [
            ("8-K", _days_ago(12), ACC_NOTES, "8.01,9.01"),
            ("8-K", _days_ago(19), ACC_DECK, "7.01,9.01"),
            ("8-K", _days_ago(19), ACC_RESULTS, "2.02,9.01"),
            ("10-Q", _days_ago(86), ACC_10Q, ""),
        ]
    )
    events = parse_recent_filings(payload, "TEST", CIK, lookback_days=90, max_events=20)

    # V2 council path: catalyst discovery → dict events → connector filing dicts.
    via_catalyst = sec_filings_from_catalyst(
        {"filing_events": [e.to_report_dict() for e in events]}
    )
    # Source-connector API path: CatalystEvent → connector filing dict.
    via_live = [live_fetchers._filing_dict(e) for e in events]

    for filings in (via_catalyst, via_live):
        ordered = _sorted_filings(filings)
        first_8k = next(f for f in ordered if f["form_type"] == "8-K")
        assert first_8k["accession_number"] == ACC_RESULTS
        assert first_8k["item_numbers"] == ["2.02", "9.01"]
        # With the default two-body cap the 10-Q and the earnings 8-K are read.
        assert [f["accession_number"] for f in ordered[:2]] == [ACC_10Q, ACC_RESULTS]


def test_a_6k_filer_without_any_items_field_flows_through_unchanged():
    payload = _submissions(
        [
            ("6-K", _days_ago(4), "0000555555-26-000003", None),
            ("6-K", _days_ago(50), "0000555555-26-000001", None),
        ]
    )
    events = parse_recent_filings(payload, "TEST", CIK, lookback_days=90, max_events=20)
    filings = [live_fetchers._filing_dict(e) for e in events]
    assert all(f["item_numbers"] == [] for f in filings)
    ordered = _sorted_filings(filings)
    assert ordered[0]["accession_number"] == "0000555555-26-000003"


# --------------------------------------------------------------------------- #
# The artifact label the ingestion records
# --------------------------------------------------------------------------- #


def _doc(accession: str, items: tuple[str, ...], form: str = "8-K") -> SecFilingDocument:
    return SecFilingDocument(
        accession_number=accession,
        form_type=form,
        filing_date="2026-10-20",
        canonical_url=(
            f"https://www.sec.gov/Archives/edgar/data/123456/{accession.replace('-', '')}/body.htm"
        ),
        document_name="body.htm",
        cik=CIK,
        title=f"SEC {form} filing",
        item_codes=items,
    )


@pytest.mark.parametrize(
    ("items", "form", "expected"),
    [
        (("8.01", "9.01"), "8-K", DOC_KIND_OTHER),
        (("2.02", "9.01"), "8-K", DOC_KIND_RESULTS_RELEASE),
        ((), "8-K", DOC_KIND_RESULTS_RELEASE),
        ((), "6-K", DOC_KIND_INTERIM_REPORT),
    ],
)
def test_ingested_artifact_doc_kind_follows_item_codes(monkeypatch, items, form, expected):
    doc = _doc(ACC_NOTES, items, form)

    async def _resolve_stub(cik, filings, *, max_documents, **kwargs):  # noqa: ANN001
        return [doc]

    async def _no_body(d, **kwargs):  # noqa: ANN001
        return None  # an honest fetch failure; the label is still recorded

    monkeypatch.setattr(live_fetchers, "resolve_filing_documents", _resolve_stub)
    monkeypatch.setattr(live_fetchers, "fetch_filing_body", _no_body)
    cfg = SimpleNamespace(
        primary_document_ingestion_enabled=True,
        primary_document_sec_body_enabled=True,
        primary_document_sec_max_bodies=2,
        sec_request_min_interval_ms=0,
    )
    artifacts = asyncio.run(
        live_fetchers.live_sec_primary_document_extractor(
            CIK, [_filing(form, ACC_NOTES, "2026-10-20")], cfg=cfg
        )
    )
    assert len(artifacts) == 1
    assert artifacts[0].doc_kind == expected
