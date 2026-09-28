"""What kind of document a disclosure is, and how useful it is to research.

Built on the platform's existing category vocabulary (``disclosure_events``) rather than
a second one. Two decisions are made here and nowhere else:

* **Document kind.** Only a periodic REPORT is ever more than ``other``: an annual
  report, an interim / half-year report, a quarterly report or results release. A JORC
  statement, a technical update or a project announcement is ``other`` — never an
  annual report — however much it says about the business.
* **Research rank.** Periodic reports first, then material announcements, then ordinary
  ones, and administrative notices last (director dealings, holdings, voting rights,
  meeting notices, quotation paperwork). Nothing is discarded; the bounded acquisition
  budget is simply spent in this order.

The VENUE's own label wins over a headline whenever it states one, exactly as
``classify_event`` already does.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.services.sources.disclosure_documents import _is_notice_about_a_filing
from app.services.sources.disclosure_events import classify_event
from app.services.sources.disclosures.model import (
    RANK_ADMINISTRATIVE,
    RANK_ANNUAL,
    RANK_INTERIM,
    RANK_MATERIAL,
    RANK_ORDINARY,
    RANK_PERIODIC,
)
from app.services.sources.document_discovery import (
    DOC_KIND_ANNUAL_REPORT,
    DOC_KIND_INTERIM_REPORT,
    DOC_KIND_OTHER,
    DOC_KIND_RESULTS_RELEASE,
)

# ── UK: the FCA NSM ``type`` vocabulary, as measured (2026-09-27) ──────────── #
_UK_ADMIN_TYPES = (
    "holding(s) in company", "director/pdmr", "pdmr", "total voting rights",
    "notice of gm", "notice of agm", "result of agm", "result of gm", "block listing",
    "transaction in own shares", "change of adviser", "directorate change",
    "form 8", "rule 2.9", "grant of share", "director declaration", "change of name",
    "annual information update", "doc re.", "publication of a prospectus",
)
_UK_MATERIAL_TYPES = (
    "statement re", "strategy/company/ operations update", "inside information",
    "miscellaneous", "acquisition", "disposal", "contract", "fundraising", "placing",
    "offer for", "trading statement", "business update", "operational update",
    "issue of equity",
)

# ── ASX: headline patterns (the yearly listing states no type column) ──────── #
_ASX_ANNUAL_RE = re.compile(r"\bannual report\b", re.I)
_ASX_NOT_THE_REPORT_RE = re.compile(
    r"corporate governance|appendix 4g|notice of|letter to shareholders|access to|"
    r"proxy|sustainab|\besg\b|tenement|webinar|presentation|briefing|conference call",
    re.I)
_ASX_INTERIM_RE = re.compile(
    r"half[- ]?year(?:ly)?\b|appendix 4d\b|interim (?:financial )?report|half year accounts",
    re.I)
_ASX_PERIODIC_RE = re.compile(
    r"quarterly|appendix 4c\b|appendix 5b\b|appendix 4e\b|preliminary final report",
    re.I)
_ASX_ADMIN_RE = re.compile(
    r"appendix (?:2a|3b|3g|3h|3x|3y|3z)\b|change of director|director'?s interest|"
    r"ceasing to be a substantial|becoming a substantial|change in substantial|"
    r"substantial holder|cleansing notice|trading halt|reinstatement to|"
    r"notice of (?:annual )?general meeting|proxy form|results? of (?:annual )?"
    r"(?:general )?meeting|application for quotation|proposed issue of securities|"
    r"quotation of securities|daily share buy-?back|company secretary|"
    r"change of (?:registered|principal)|corporate governance statement|"
    r"notification of cessation|notification regarding unquoted|pause in trading|"
    r"securities trading policy|constitution\b",
    re.I)

# UK headlines that are periodic reports whatever the NSM type says.
_UK_FINAL_RESULTS_RE = re.compile(
    r"\b(?:final|full[- ]year|annual|preliminary)\s+results\b(?!\s+(?:of|date))", re.I)
#: A periodic TRADING statement. Not "Q1 2028 update": a quarter named in a headline is
#: as likely a forecast as a report.
_UK_QUARTERLY_RE = re.compile(
    r"\bquarterly\s+(?:results|report|update|trading)|trading update|"
    r"interim management statement", re.I)
_UK_NOT_RESULTS_RE = re.compile(r"retail offer|results? date|notice of", re.I)


def _has(label: str, needles: Iterable[str]) -> bool:
    return any(n in label for n in needles)


#: NSM ``document_format`` values that ARE a filed report rather than an RNS text.
_UK_REPORT_FORMATS = frozenset({"pdf", "tagged", "untagged"})


def classify_uk(*, nsm_type: str, headline: str, document_format: str) -> tuple[str, str, int]:
    """``(category, doc_kind, rank)`` for one NSM record."""
    label = (nsm_type or "").strip().lower()
    fmt = (document_format or "").strip().lower()
    category = classify_event(nsm_type, headline)
    if "annual financial report" in label:
        # The REPORT — a PDF, or an ESEF tagged / untagged XHTML filing — not the RNS
        # "Publication of Annual Report …" notice the NSM files under the same type as
        # plain text. Measured on Pensana: both appear, on the same day.
        if fmt in _UK_REPORT_FORMATS and not _is_notice_about_a_filing(headline or ""):
            return category, DOC_KIND_ANNUAL_REPORT, RANK_ANNUAL
        # A text filing under this type is either the full-year results RNS (the
        # headline says so, below) or the notice of publication (ordinary).
    if "half-year" in label or "half year" in label or "half yearly" in label:
        return category, DOC_KIND_INTERIM_REPORT, RANK_INTERIM
    head = headline or ""
    if not _UK_NOT_RESULTS_RE.search(head) and (
        _UK_FINAL_RESULTS_RE.search(head) or _UK_QUARTERLY_RE.search(head)
    ):
        return category, DOC_KIND_RESULTS_RELEASE, RANK_PERIODIC
    if _has(label, _UK_ADMIN_TYPES):
        return category, DOC_KIND_OTHER, RANK_ADMINISTRATIVE
    if _has(label, _UK_MATERIAL_TYPES):
        return category, DOC_KIND_OTHER, RANK_MATERIAL
    return category, DOC_KIND_OTHER, RANK_ORDINARY


def classify_asx(*, headline: str, price_sensitive: bool | None) -> tuple[str, str, int]:
    """``(category, doc_kind, rank)`` for one ASX announcement."""
    head = headline or ""
    category = classify_event(None, head)
    if _ASX_ANNUAL_RE.search(head) and not _ASX_NOT_THE_REPORT_RE.search(head):
        return category, DOC_KIND_ANNUAL_REPORT, RANK_ANNUAL
    if _ASX_INTERIM_RE.search(head) and not _ASX_NOT_THE_REPORT_RE.search(head):
        return category, DOC_KIND_INTERIM_REPORT, RANK_INTERIM
    if _ASX_PERIODIC_RE.search(head):
        return category, DOC_KIND_RESULTS_RELEASE, RANK_PERIODIC
    if _ASX_ADMIN_RE.search(head):
        return category, DOC_KIND_OTHER, RANK_ADMINISTRATIVE
    # The exchange's own price-sensitive marker: the issuer told the market this
    # announcement could move the price. Project, financing, offtake and resource
    # announcements carry it; notices do not.
    if price_sensitive:
        return category, DOC_KIND_OTHER, RANK_MATERIAL
    return category, DOC_KIND_OTHER, RANK_ORDINARY


_TOPIC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &'-]{1,39}$")


def clean_topics(raw: Iterable[str] | None, *, limit: int = 5) -> tuple[str, ...]:
    """Bounded, validated topic words a specialist may use to prioritise headlines."""
    out: list[str] = []
    for value in raw or ():
        text = str(value or "").strip()
        if _TOPIC_RE.match(text) and text.lower() not in (t.lower() for t in out):
            out.append(text)
        if len(out) >= limit:
            break
    return tuple(out)


def topic_boost(headline: str, topics: Iterable[str]) -> int:
    """1 when the headline names a requested topic (as whole words), else 0."""
    head = (headline or "").lower()
    for topic in topics:
        if re.search(r"\b" + re.escape(topic.lower()) + r"\b", head):
            return 1
    return 0


#: A FULL-YEAR results announcement: the narrative of the year the annual report covers
#: (an issuer's annual report can carry its narrative only as page images — Rainbow's
#: 2025 iXBRL report). Not "results of AGM", not a results date.
_FULL_YEAR_RESULTS_RE = re.compile(
    r"\b(?:final|full[- ]year|annual|preliminary)\s+results\b(?!\s+(?:of|date))"
    r"|\bresults\s+for\s+the\s+(?:financial\s+)?year\s+ended\b"
    r"|\bfy\s?'?\d{2}(?:\d{2})?\s+results\b"
    r"|\bpreliminary\s+final\s+report\b|\bappendix\s+4e\b", re.I)
#: About the results, not the results: a trading update "ahead of" them, the deck, the
#: call (review: "Trading Update ahead of Full Year Results" took the slot).
_NOT_THE_RESULTS_RE = re.compile(
    r"\b(?:trading|ahead of|presentation|webcast|webinar|call|briefing|conference|"
    r"notice|date|retail offer|agm|general meeting)\b", re.I)


def is_full_year_results(headline: str | None) -> bool:
    """True for a full-year results announcement headline."""
    text = headline or ""
    return bool(_FULL_YEAR_RESULTS_RE.search(text)) and not _NOT_THE_RESULTS_RE.search(text)


__all__ = ["classify_asx", "classify_uk", "clean_topics", "is_full_year_results",
           "topic_boost"]
