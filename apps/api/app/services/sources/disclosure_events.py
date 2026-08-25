"""ONE normalized model for a regulated disclosure, and semantic dedupe for it.

Private-use production readiness, PR-E.

The existing venue connectors (``nordic_disclosures``, ``six_swiss``,
``euronext_regulated_info``, ``uk_fca_nsm``) are reference-only by design: each
emits a pointer to the issuer's regulated-disclosure venue plus an honest gap
saying the filing CONTENT is not fetched. For a private research system that is
half an answer — a researcher asking "what did this issuer just announce?" gets
a link to a search page.

This module supplies the missing half's CONTRACT. Every venue that can be
retrieved live normalises into one ``DisclosureEvent``, so:

  * the report, the council pack and the DFR all read the same shape;
  * an issuer's own newsroom item and the exchange's copy of the SAME
    announcement merge into ONE event carrying BOTH provenances, rather than
    appearing two or three times;
  * a venue that cannot be retrieved degrades to the existing honest reference
    without any consumer noticing a different shape.

What this module deliberately does NOT do: decide that an event is material,
bullish, bearish, or a reason to trade. Category is a *venue-stated* label
where the venue states one. Nothing here is a recommendation.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services.sources.taxonomy import tier_rank

# ── Event categories ─────────────────────────────────────────────────────── #
#
# Deliberately coarse and descriptive. These describe what KIND of disclosure a
# venue published, never its investment significance.

EVENT_CATEGORY_RESULTS = "results"
EVENT_CATEGORY_GUIDANCE = "guidance"
EVENT_CATEGORY_MANAGEMENT = "management"
EVENT_CATEGORY_CAPITAL_STRUCTURE = "capital_structure"
EVENT_CATEGORY_TRANSACTION = "transaction"
EVENT_CATEGORY_REGULATORY = "regulatory"
EVENT_CATEGORY_SHAREHOLDING = "shareholding"
EVENT_CATEGORY_GOVERNANCE = "governance"
EVENT_CATEGORY_OTHER = "other"

VALID_EVENT_CATEGORIES: frozenset[str] = frozenset(
    {
        EVENT_CATEGORY_RESULTS,
        EVENT_CATEGORY_GUIDANCE,
        EVENT_CATEGORY_MANAGEMENT,
        EVENT_CATEGORY_CAPITAL_STRUCTURE,
        EVENT_CATEGORY_TRANSACTION,
        EVENT_CATEGORY_REGULATORY,
        EVENT_CATEGORY_SHAREHOLDING,
        EVENT_CATEGORY_GOVERNANCE,
        EVENT_CATEGORY_OTHER,
    }
)

# Venue-stated category text -> our coarse category. Matched as a substring of
# the venue's OWN label, lower-cased. A venue that states nothing recognisable
# yields ``other`` — never a guess dressed up as a classification.
_VENUE_CATEGORY_HINTS: tuple[tuple[str, str], ...] = (
    ("interim report", EVENT_CATEGORY_RESULTS),
    ("half year financial report", EVENT_CATEGORY_RESULTS),
    ("half-year", EVENT_CATEGORY_RESULTS),
    ("annual financial report", EVENT_CATEGORY_RESULTS),
    ("quarterly report", EVENT_CATEGORY_RESULTS),
    ("financial statement", EVENT_CATEGORY_RESULTS),
    ("financial report", EVENT_CATEGORY_RESULTS),
    ("financial results", EVENT_CATEGORY_RESULTS),
    ("guidance", EVENT_CATEGORY_GUIDANCE),
    ("outlook", EVENT_CATEGORY_GUIDANCE),
    ("managers' transactions", EVENT_CATEGORY_SHAREHOLDING),
    ("managers transactions", EVENT_CATEGORY_SHAREHOLDING),
    ("major shareholder", EVENT_CATEGORY_SHAREHOLDING),
    ("total number of voting rights", EVENT_CATEGORY_CAPITAL_STRUCTURE),
    ("share capital", EVENT_CATEGORY_CAPITAL_STRUCTURE),
    ("share buyback", EVENT_CATEGORY_CAPITAL_STRUCTURE),
    ("buy-back", EVENT_CATEGORY_CAPITAL_STRUCTURE),
    ("dividend", EVENT_CATEGORY_CAPITAL_STRUCTURE),
    ("general meeting", EVENT_CATEGORY_GOVERNANCE),
    ("shareholders' meeting", EVENT_CATEGORY_GOVERNANCE),
    ("board of directors", EVENT_CATEGORY_GOVERNANCE),
    ("admission to listing", EVENT_CATEGORY_REGULATORY),
    ("prospectus", EVENT_CATEGORY_REGULATORY),
    ("inside information", EVENT_CATEGORY_OTHER),
)

# Title hints, used ONLY when the venue states no category of its own. A
# headline is weaker evidence than a venue's structured label, so it is
# consulted second and never overrides one.
_TITLE_CATEGORY_HINTS: tuple[tuple[str, str], ...] = (
    ("interim report", EVENT_CATEGORY_RESULTS),
    ("interim financial report", EVENT_CATEGORY_RESULTS),
    ("half-year", EVENT_CATEGORY_RESULTS),
    ("half year", EVENT_CATEGORY_RESULTS),
    ("first half", EVENT_CATEGORY_RESULTS),
    ("full year results", EVENT_CATEGORY_RESULTS),
    ("annual results", EVENT_CATEGORY_RESULTS),
    ("annual report", EVENT_CATEGORY_RESULTS),
    ("financial results", EVENT_CATEGORY_RESULTS),
    ("quarterly", EVENT_CATEGORY_RESULTS),
    ("guidance", EVENT_CATEGORY_GUIDANCE),
    ("appoints", EVENT_CATEGORY_MANAGEMENT),
    ("appointment", EVENT_CATEGORY_MANAGEMENT),
    ("resigns", EVENT_CATEGORY_MANAGEMENT),
    ("steps down", EVENT_CATEGORY_MANAGEMENT),
    ("acquisition", EVENT_CATEGORY_TRANSACTION),
    ("acquires", EVENT_CATEGORY_TRANSACTION),
    ("divestment", EVENT_CATEGORY_TRANSACTION),
    ("disposal", EVENT_CATEGORY_TRANSACTION),
    ("dividend", EVENT_CATEGORY_CAPITAL_STRUCTURE),
    ("share buyback", EVENT_CATEGORY_CAPITAL_STRUCTURE),
    ("share capital", EVENT_CATEGORY_CAPITAL_STRUCTURE),
    ("general meeting", EVENT_CATEGORY_GOVERNANCE),
    ("shareholders' meeting", EVENT_CATEGORY_GOVERNANCE),
    ("shareholders meeting", EVENT_CATEGORY_GOVERNANCE),
    ("board of directors", EVENT_CATEGORY_GOVERNANCE),
    ("board of statutory auditors", EVENT_CATEGORY_GOVERNANCE),
    ("voting rights", EVENT_CATEGORY_CAPITAL_STRUCTURE),
    # Venue-standard Italian phrasing. The Italian storage mechanism publishes
    # an Italian edition of every announcement alongside the English one; these
    # are the venue's own standard headings, not an issuer's vocabulary.
    ("relazione finanziaria semestrale", EVENT_CATEGORY_RESULTS),
    ("relazione finanziaria annuale", EVENT_CATEGORY_RESULTS),
    ("risultati primo semestre", EVENT_CATEGORY_RESULTS),
    ("risultati", EVENT_CATEGORY_RESULTS),
    ("bilancio", EVENT_CATEGORY_RESULTS),
    ("assemblea", EVENT_CATEGORY_GOVERNANCE),
    ("dividendo", EVENT_CATEGORY_CAPITAL_STRUCTURE),
    ("capitale sociale", EVENT_CATEGORY_CAPITAL_STRUCTURE),
    ("acquisto di azioni proprie", EVENT_CATEGORY_CAPITAL_STRUCTURE),
)

# ── Bounds ───────────────────────────────────────────────────────────────── #

#: Hard caps applied to EVERY venue, whatever its own paging behaviour.
MAX_EVENTS_PER_ISSUER = 25
MAX_ATTACHMENTS_PER_EVENT = 4
MAX_TITLE_CHARS = 300
MAX_CATEGORY_CHARS = 120
MAX_URL_CHARS = 2000
#: Default lookback. A regulated-disclosure feed is a CURRENT-state signal; a
#: multi-year backfill is a different (and much more expensive) product.
DEFAULT_LOOKBACK_DAYS = 400

_WS_RE = re.compile(r"\s+")
#: Boilerplate that varies between an issuer's own wording and an exchange's
#: rendering of the SAME announcement, and must not defeat the dedupe.
_TITLE_NOISE_RE = re.compile(
    r"\b(company announcement|announcement|press release|regulatory news|"
    r"inside information|ad hoc announcement|ad-hoc announcement|"
    r"pursuant to art\.? ?53 lr|no\.?|nr\.?)\b",
    re.IGNORECASE,
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
#: A leading announcement/reference number left behind once the boilerplate is
#: stripped ("Company Announcement No. 1015: <headline>"). The number is the
#: exchange's own reference for the SAME headline the issuer published without
#: one, so leaving it in the key defeats exactly the dedupe it should enable.
#: Only a LEADING run is stripped — a number inside the headline ("3% organic
#: growth", "Q2") is content and stays.
_LEADING_REF_RE = re.compile(r"^(?:[\s:;,.\-–—/#]*\d+)+[\s:;,.\-–—/#]*")


def _clip(value: object, limit: int) -> str | None:
    if value is None:
        return None
    text = _WS_RE.sub(" ", str(value)).strip()
    return text[:limit] if text else None


def normalize_title(title: str | None) -> str:
    """A comparison key for a headline, robust to how a venue renders it.

    Strips accents, announcement boilerplate and announcement numbers, then
    collapses to alphanumerics. This is what lets "Pandora delivers 3% organic
    growth in Q2 - guidance upgraded" from the issuer's newsroom match the same
    headline carried by the exchange under "Company Announcement No. 1015".
    """
    if not title:
        return ""
    text = unicodedata.normalize("NFKD", str(title))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = _TITLE_NOISE_RE.sub(" ", text)
    text = _LEADING_REF_RE.sub("", text.strip())
    return _NON_ALNUM_RE.sub("", text)


def classify_event(
    venue_category: str | None, title: str | None
) -> str:
    """Coarse, descriptive category. Never an investment judgement.

    The VENUE's own structured label wins; a headline is only consulted when the
    venue states nothing. An unrecognised disclosure is ``other`` — this never
    invents a category to look more complete.
    """
    label = (venue_category or "").strip().lower()
    if label:
        for needle, category in _VENUE_CATEGORY_HINTS:
            if needle in label:
                # A venue label that maps to ``other`` is a REGULATORY
                # classification rather than a content one ("Inside
                # information" says how the disclosure is regulated, not what
                # it is about), so the headline is still allowed to refine it.
                # A venue label that maps to a real category always wins.
                if category != EVENT_CATEGORY_OTHER:
                    return category
                break
    head = (title or "").strip().lower()
    if head:
        for needle, category in _TITLE_CATEGORY_HINTS:
            if needle in head:
                return category
    return EVENT_CATEGORY_OTHER


@dataclass
class DisclosureEvent:
    """One regulated disclosure, from any venue, in one shape.

    ``provenances`` is a LIST because the same announcement legitimately reaches
    the system through more than one channel (the issuer's newsroom, the
    exchange, the national storage mechanism). Merging them must never discard
    a channel: which venues carried an announcement is itself evidence about
    how well-sourced it is.
    """

    issuer_ticker: str | None
    issuer_name: str | None
    venue: str
    country: str | None
    published_at: datetime | None
    title: str | None
    category: str = EVENT_CATEGORY_OTHER
    #: The venue's OWN category label, kept verbatim — never replaced by ours.
    venue_category: str | None = None
    language: str | None = None
    official_url: str | None = None
    attachment_urls: tuple[str, ...] = ()
    document_identifier: str | None = None
    period: str | None = None
    source_tier: str = "T2_regulator_or_gov"
    retrieved_at: datetime | None = None
    provenances: tuple[str, ...] = ()
    #: True when a MODEL produced any part of this record. Always False here —
    #: every field is read from the venue's own payload.
    model_derived: bool = False
    #: Original-language title, preserved whenever a translation is displayed.
    original_title: str | None = None
    translated_title: str | None = None

    def __post_init__(self) -> None:
        self.title = _clip(self.title, MAX_TITLE_CHARS)
        self.venue_category = _clip(self.venue_category, MAX_CATEGORY_CHARS)
        self.official_url = _clip(self.official_url, MAX_URL_CHARS)
        clipped = [_clip(a, MAX_URL_CHARS) for a in self.attachment_urls]
        self.attachment_urls = tuple(
            u for u in clipped if u
        )[:MAX_ATTACHMENTS_PER_EVENT]
        if self.category not in VALID_EVENT_CATEGORIES:
            self.category = EVENT_CATEGORY_OTHER
        if self.published_at is not None and self.published_at.tzinfo is None:
            self.published_at = self.published_at.replace(tzinfo=timezone.utc)

    # -- identity ---------------------------------------------------------- #

    @property
    def date_key(self) -> str | None:
        return self.published_at.date().isoformat() if self.published_at else None

    @property
    def dedupe_key(self) -> tuple[str, str, str]:
        """Stable SEMANTIC identity: (issuer, publication date, normalized title).

        Deliberately date-level, not timestamp-level: an issuer's newsroom and
        the exchange routinely stamp the same announcement minutes apart, and a
        second-precision key would leave the duplicate in place. Deliberately
        NOT url-based: the two channels host it at different URLs, which is the
        whole reason it appears twice.
        """
        issuer = (self.issuer_ticker or self.issuer_name or "").strip().lower()
        return (issuer, self.date_key or "", normalize_title(self.title))

    @property
    def is_identifiable(self) -> bool:
        """False when the event lacks enough identity to dedupe honestly.

        Such an event is KEPT (it is real evidence) but is never merged into
        another, because merging on a partial key could silently collapse two
        genuinely different announcements.
        """
        issuer, date_key, title_key = self.dedupe_key
        return bool(issuer and date_key and title_key)

    def display_title(self) -> str:
        """Never emits ``None`` into human-facing text."""
        return self.title or "Untitled disclosure"


@dataclass
class DisclosureFeed:
    """One venue's bounded result for one issuer, with its own honest limits."""

    venue: str
    events: list[DisclosureEvent] = field(default_factory=list)
    #: Why the feed is empty / partial, in machine-readable form.
    limitations: list[str] = field(default_factory=list)
    retrieved_at: datetime | None = None
    #: True only when a live retrieval actually succeeded.
    live: bool = False

    @property
    def event_count(self) -> int:
        return len(self.events)


def merge_events(
    feeds: "list[DisclosureFeed] | list[list[DisclosureEvent]]",
) -> list[DisclosureEvent]:
    """Merge every feed into ONE deduplicated, newest-first event list.

    The same announcement reaching the system through the issuer's newsroom AND
    the exchange becomes ONE event carrying BOTH provenances. The surviving
    record prefers, field by field:

      * the strongest SOURCE TIER (a regulator/exchange copy over an
        aggregator's), because that is the copy a reader should cite;
      * a present value over an absent one, for every optional field, so no
        channel's extra detail (an attachment, an announcement number, a
        period) is lost just because the other channel won the tier contest.

    Provenance is never discarded: which venues carried an announcement is
    itself evidence about how well-sourced it is.
    """
    events: list[DisclosureEvent] = []
    for feed in feeds or []:
        if isinstance(feed, DisclosureFeed):
            events.extend(feed.events)
        else:
            events.extend(feed)

    merged: dict[tuple[str, str, str], DisclosureEvent] = {}
    unmergeable: list[DisclosureEvent] = []
    for event in events:
        if not event.is_identifiable:
            unmergeable.append(event)
            continue
        key = event.dedupe_key
        existing = merged.get(key)
        if existing is None:
            merged[key] = event
            continue
        merged[key] = _merge_pair(existing, event)

    out = list(merged.values()) + unmergeable
    out.sort(
        key=lambda e: (e.published_at or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )
    return out[:MAX_EVENTS_PER_ISSUER]


def _merge_pair(a: DisclosureEvent, b: DisclosureEvent) -> DisclosureEvent:
    """Combine two records of the SAME announcement. Never loses a provenance."""
    primary, secondary = (
        (a, b) if tier_rank(a.source_tier) <= tier_rank(b.source_tier) else (b, a)
    )
    provenances = tuple(
        dict.fromkeys([*primary.provenances, *secondary.provenances])
    )
    venues = " + ".join(dict.fromkeys([primary.venue, secondary.venue]))
    attachments = tuple(
        dict.fromkeys([*primary.attachment_urls, *secondary.attachment_urls])
    )[:MAX_ATTACHMENTS_PER_EVENT]
    return DisclosureEvent(
        issuer_ticker=primary.issuer_ticker or secondary.issuer_ticker,
        issuer_name=primary.issuer_name or secondary.issuer_name,
        venue=venues,
        country=primary.country or secondary.country,
        published_at=primary.published_at or secondary.published_at,
        title=primary.title or secondary.title,
        category=(
            primary.category
            if primary.category != EVENT_CATEGORY_OTHER
            else secondary.category
        ),
        venue_category=primary.venue_category or secondary.venue_category,
        language=primary.language or secondary.language,
        official_url=primary.official_url or secondary.official_url,
        attachment_urls=attachments,
        document_identifier=(
            primary.document_identifier or secondary.document_identifier
        ),
        period=primary.period or secondary.period,
        source_tier=primary.source_tier,
        retrieved_at=primary.retrieved_at or secondary.retrieved_at,
        provenances=provenances,
        model_derived=primary.model_derived or secondary.model_derived,
        original_title=primary.original_title or secondary.original_title,
        translated_title=primary.translated_title or secondary.translated_title,
    )


__all__ = [
    "DEFAULT_LOOKBACK_DAYS",
    "EVENT_CATEGORY_CAPITAL_STRUCTURE",
    "EVENT_CATEGORY_GOVERNANCE",
    "EVENT_CATEGORY_GUIDANCE",
    "EVENT_CATEGORY_MANAGEMENT",
    "EVENT_CATEGORY_OTHER",
    "EVENT_CATEGORY_REGULATORY",
    "EVENT_CATEGORY_RESULTS",
    "EVENT_CATEGORY_SHAREHOLDING",
    "EVENT_CATEGORY_TRANSACTION",
    "MAX_ATTACHMENTS_PER_EVENT",
    "MAX_EVENTS_PER_ISSUER",
    "VALID_EVENT_CATEGORIES",
    "DisclosureEvent",
    "DisclosureFeed",
    "classify_event",
    "merge_events",
    "normalize_title",
]
