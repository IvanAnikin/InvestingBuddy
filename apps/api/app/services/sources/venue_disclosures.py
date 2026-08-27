"""BOUNDED LIVE retrieval of regulated disclosures from official venues.

Private-use production readiness, PR-E.

The existing venue connectors are reference-only: each emits a pointer to the
issuer's regulated-disclosure venue plus an honest gap saying the filing CONTENT
is not fetched. This module supplies live retrieval for the venues that offer a
legitimate, officially-published machine-readable surface, normalising every one
into the single ``DisclosureEvent`` contract.

Researched live on 2026-08-25. Two venues qualified:

  * **Nasdaq Nordic** (Copenhagen / Stockholm / Helsinki / Oslo) —
    ``api.news.eu.nasdaq.com/news/query.action`` is the exchange's own
    company-news service. It returns JSON with the issuer name, release time,
    headline, the venue's own disclosure category, an official view URL, and
    typed attachments. This is the richest official surface in the target
    universe.
  * **eMarket Storage** (Italy) — the CONSOB-authorised storage mechanism
    operated by Teleborsa. Its per-issuer listing carries a dated row per
    announcement with a direct link to the official PDF.

Three venues did NOT qualify and are recorded honestly rather than worked
around, per the campaign's source-terms rule:

  * **SIX Swiss** — no public per-issuer disclosure API was found. Swiss
    issuers publish their Art. 53 LR ad-hoc announcements on their own sites,
    which the issuer-primary path already reaches.
  * **Euronext Paris** — ``live.euronext.com`` renders company news into a
    modal and paginates it; the server-rendered page carries only a handful of
    rows.
  * **LSE / FCA NSM** — the NSM portal returns 403 and its search API rejects
    every documented index name. Burberry's own site is behind a proof-of-work
    challenge. **Neither is bypassed.**

Everything here is bounded the same way the rest of the fetch layer is: an
exact host allowlist, the SSRF/DNS/TLS/redirect-guarded ``safe_fetch_page``, a
lookback window, an item cap, a byte cap and a wall-clock budget. A venue that
fails degrades to an honest limitation — never a fabricated announcement.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.services.sources.disclosure_events import (
    DisclosureEvent,
    DisclosureFeed,
    classify_event,
)
from app.services.sources.safe_web_fetcher import safe_fetch_page
from app.services.sources.taxonomy import T2_REGULATOR_OR_GOV

# ── Venue identities ─────────────────────────────────────────────────────── #

VENUE_NASDAQ_NORDIC = "Nasdaq Nordic company news"
VENUE_EMARKET_STORAGE = "eMarket Storage (CONSOB-authorised)"

#: EXACT hosts. Never widened to make one issuer work — a venue that needs a
#: different host is a different venue and gets its own explicit entry.
_NASDAQ_NEWS_HOST = "api.news.eu.nasdaq.com"
_NASDAQ_ALLOWED = (
    _NASDAQ_NEWS_HOST,
    "view.news.eu.nasdaq.com",
    "attachment.news.eu.nasdaq.com",
)
_EMARKET_HOST = "www.emarketstorage.it"
_EMARKET_ALLOWED = (_EMARKET_HOST, "emarketstorage.it")

#: The hosts a venue's own DOCUMENTS live on. Exported so a caller that wants
#: to open one (current-period acceptance) fetches it under the SAME explicit
#: allowlist the listing was retrieved under — never under a host taken from
#: the URL it is about to fetch.
NASDAQ_NORDIC_DOCUMENT_DOMAINS: tuple[str, ...] = _NASDAQ_ALLOWED
EMARKET_STORAGE_DOCUMENT_DOMAINS: tuple[str, ...] = _EMARKET_ALLOWED

_NASDAQ_QUERY_URL = f"https://{_NASDAQ_NEWS_HOST}/news/query.action"
_EMARKET_LIST_URL = f"https://{_EMARKET_HOST}/it/comunicati-finanziari"

#: Nasdaq Nordic market strings, per venue code. Only venues with a mapping are
#: eligible — an unmapped venue yields an honest limitation, never a guess.
NASDAQ_MARKETS: dict[str, str] = {
    "CO": "Main Market, Copenhagen",
    "ST": "Main Market, Stockholm",
    "HE": "Main Market, Helsinki",
    "OL": "Main Market, Oslo",
}

#: eMarket Storage issuer ids, per ticker. The venue exposes its issuer filter
#: as an opaque numeric id with no derivable relationship to the ticker, so the
#: mapping is CURATED — exactly like the verified-issuer registry, and for the
#: same reason: the trust relationship is issuer-specific even though every
#: line of parsing below is generic.
EMARKET_ISSUER_IDS: dict[str, str] = {
    "MONC": "80007",
}

# ── Bounds ───────────────────────────────────────────────────────────────── #

#: Items requested from a venue in ONE call. The venue's own paging is never
#: followed past this: there is no second page request anywhere in this module.
_VENUE_PAGE_SIZE = 30
#: Rows parsed out of one venue response, whatever the venue returned.
_MAX_ROWS_PARSED = 200
#: Bytes of a venue response that are parsed. Deliberately independent of the
#: fetch-layer cap so a venue change cannot silently widen it.
_MAX_RESPONSE_CHARS = 2_000_000

_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")
_ROW_RE = re.compile(
    r'<div class="views-row">(.*?)(?=<div class="views-row">|<nav|</section)', re.S
)
_HREF_RE = re.compile(r'href="([^"]+)"')
_EMARKET_DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})(?:\s*-\s*(\d{2}):(\d{2}))?")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _strip_tags(fragment: str) -> str:
    """Tags out, entities decoded. A headline rendered as
    ``dell&#039;Assemblea`` must reach a human as ``dell'Assemblea`` — an
    un-decoded entity is markup leaking into human-facing text, the same class
    of defect as a ``None`` literal reaching a report."""
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", fragment))).strip()


def _limitation(reason: str, detail: str | None = None) -> str:
    return f"{reason}: {detail}" if detail else reason


# --------------------------------------------------------------------------- #
# Nasdaq Nordic
# --------------------------------------------------------------------------- #


#: Corporate legal-form suffixes. Standard company-law vocabulary across the
#: target jurisdictions — not an issuer-specific list.
_LEGAL_FORM_SUFFIXES: tuple[str, ...] = (
    "a/s", "aktieselskab", "asa", "ab", "abp", "oyj", "oy",
    "sa", "s.a.", "s.a", "sca", "s.c.a.", "se", "spa", "s.p.a.", "s.p.a",
    "nv", "n.v.", "bv", "b.v.", "ag", "a.g.", "gmbh", "kgaa",
    "plc", "p.l.c.", "ltd", "limited", "inc", "inc.", "corp", "corporation",
)
# NOTE: "Group" / "Holding" / "International" are deliberately NOT here. They
# look like suffixes but they are part of the NAME — "Burberry Group" and "The
# Swatch Group" are what those issuers are called, and trimming them would
# widen the search past the issuer rather than past its legal form.


def issuer_search_term(issuer_name: str) -> str:
    """The name to SEARCH a venue with, given an issuer's full legal name.

    Live-acceptance corrective (2026-08-26). The Nasdaq Nordic service's
    ``freeText`` matches the announcement BODY, not just the issuer field. A
    query for the full legal name "Pandora A/S" therefore returned the routine
    managers'-transaction notices — whose boilerplate title literally contains
    "Pandora A/S shares" — while SILENTLY DROPPING the two announcements a
    researcher actually wants: the Q2 2026 results ("Pandora delivers 3%
    organic growth in Q2 - guidance upgraded") and the CFO appointment. Neither
    contains the legal-form suffix, because no headline does.

    So the suffix is the part of a legal name LEAST likely to appear in the
    text being searched, and including it is not "more precise" — it is a
    filter that removes the most substantive disclosures.

    Stripping it widens the SEARCH only. Precision is preserved where it
    belongs: every returned row is still checked against the issuer's full name
    by ``_issuer_name_matches`` before it can become an event, so a foreign
    issuer that merely mentions this one is still rejected.

    Never returns an empty string — an issuer whose name is nothing but legal
    form keeps its original name rather than being searched for "".
    """
    words = _WS_RE.sub(" ", (issuer_name or "").strip()).split(" ")
    while len(words) > 1 and words[-1].strip(",.").casefold() in _LEGAL_FORM_SUFFIXES:
        words = words[:-1]
    trimmed = " ".join(words).strip(" ,.")
    return trimmed or (issuer_name or "").strip()


def _nasdaq_query_url(market: str, issuer_name: str, *, limit: int) -> str:
    """The venue's own documented query surface, with every bound set."""
    params = [
        ("type", "json"),
        ("showAttachments", "true"),
        ("showCnsSpecific", "true"),
        ("showCompany", "true"),
        ("countResults", "false"),
        # SEARCH on the trimmed name; MATCH on the full one — see
        # ``issuer_search_term``.
        ("freeText", issuer_search_term(issuer_name)),
        ("market", market),
        ("globalName", "NordicMainMarket"),
        ("displayLanguage", "en"),
        ("timeZone", "CET"),
        ("dateMask", "yyyy-MM-dd HH:mm:ss"),
        ("limit", str(limit)),
        ("start", "0"),
        ("dir", "DESC"),
    ]
    query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params)
    return f"{_NASDAQ_QUERY_URL}?{query}"


def _parse_nasdaq_time(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw).strip(), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return None


def _nasdaq_attachments(item: dict[str, Any]) -> tuple[str, ...]:
    raw = item.get("attachment")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    for entry in raw:
        if isinstance(entry, dict):
            url = entry.get("attachmentUrl")
            if isinstance(url, str) and url.startswith("https://"):
                out.append(url)
    return tuple(out)


def _issuer_name_matches(candidate: str | None, issuer_name: str | None) -> bool:
    """True when the venue row genuinely belongs to THIS issuer.

    The venue's free-text search is a search, not a filter — a query for
    "Pandora" legitimately returns another issuer's announcement that merely
    mentions it. Attributing that to Pandora would be a fabricated event, so the
    row's own company field must match. Matched on a normalised prefix so
    "Pandora A/S" matches "Pandora" without matching "Pandora Media Inc".
    """
    if not candidate or not issuer_name:
        return False
    a = _WS_RE.sub(" ", candidate).strip().casefold()
    b = _WS_RE.sub(" ", issuer_name).strip().casefold()
    return a.startswith(b) or b.startswith(a)


def parse_nasdaq_payload(
    body: str,
    *,
    issuer_ticker: str | None,
    issuer_name: str,
    country: str | None,
    cutoff: datetime | None,
    max_events: int,
) -> tuple[list[DisclosureEvent], list[str]]:
    """Parse ONE Nasdaq Nordic response. Pure; never raises."""
    limitations: list[str] = []
    if len(body) > _MAX_RESPONSE_CHARS:
        limitations.append(_limitation("venue_response_truncated", VENUE_NASDAQ_NORDIC))
        body = body[:_MAX_RESPONSE_CHARS]
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return [], [_limitation("venue_response_unparseable", VENUE_NASDAQ_NORDIC)]

    items = ((payload or {}).get("results") or {}).get("item")
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return [], [_limitation("venue_response_shape_unexpected", VENUE_NASDAQ_NORDIC)]

    events: list[DisclosureEvent] = []
    now = _utcnow()
    for item in items[:_MAX_ROWS_PARSED]:
        if len(events) >= max_events:
            break
        if not isinstance(item, dict):
            continue
        company = item.get("company")
        if not _issuer_name_matches(
            company if isinstance(company, str) else None, issuer_name
        ):
            # Another issuer's announcement that merely mentions this one.
            continue
        published = _parse_nasdaq_time(item.get("releaseTime"))
        if cutoff is not None and published is not None and published < cutoff:
            continue
        headline = item.get("headline")
        venue_category = item.get("cnsCategory")
        url = item.get("messageUrl")
        events.append(
            DisclosureEvent(
                issuer_ticker=issuer_ticker,
                issuer_name=company if isinstance(company, str) else issuer_name,
                venue=VENUE_NASDAQ_NORDIC,
                country=country,
                published_at=published,
                title=headline if isinstance(headline, str) else None,
                category=classify_event(
                    venue_category if isinstance(venue_category, str) else None,
                    headline if isinstance(headline, str) else None,
                ),
                venue_category=(
                    venue_category if isinstance(venue_category, str) else None
                ),
                language=item.get("language") if isinstance(item.get("language"), str) else None,
                official_url=url if isinstance(url, str) else None,
                attachment_urls=_nasdaq_attachments(item),
                document_identifier=(
                    str(item["disclosureId"]) if item.get("disclosureId") else None
                ),
                source_tier=T2_REGULATOR_OR_GOV,
                retrieved_at=now,
                provenances=(f"{VENUE_NASDAQ_NORDIC} (exchange-operated)",),
            )
        )
    return events, limitations


async def fetch_nasdaq_nordic_disclosures(
    *,
    issuer_ticker: str | None,
    issuer_name: str,
    exchange: str | None,
    country: str | None,
    cfg: Settings | None = None,
    max_events: int,
    lookback_days: int,
    fetcher=safe_fetch_page,
) -> DisclosureFeed:
    """Bounded live retrieval from the Nasdaq Nordic company-news service."""
    cfg = cfg or default_settings
    feed = DisclosureFeed(venue=VENUE_NASDAQ_NORDIC, retrieved_at=_utcnow())

    market = NASDAQ_MARKETS.get((exchange or "").strip().upper())
    if market is None:
        feed.limitations.append(
            _limitation("venue_not_eligible", f"exchange={exchange!r} is not a Nasdaq Nordic venue")
        )
        return feed
    if not issuer_name:
        feed.limitations.append(_limitation("issuer_identity_unresolved"))
        return feed

    url = _nasdaq_query_url(market, issuer_name, limit=min(_VENUE_PAGE_SIZE, max_events * 2))
    result = await fetcher(
        url,
        allowed_domains=_NASDAQ_ALLOWED,
        keywords=(),
        cfg=cfg,
        resolve_ip=bool(getattr(cfg, "primary_document_pin_dns_enabled", True)),
    )
    if result.blocked or result.error or not result.body_html:
        feed.limitations.append(
            _limitation("venue_unreachable", result.error or "blocked")
        )
        return feed

    cutoff = _utcnow() - timedelta(days=max(1, lookback_days))
    events, limitations = parse_nasdaq_payload(
        result.body_html,
        issuer_ticker=issuer_ticker,
        issuer_name=issuer_name,
        country=country,
        cutoff=cutoff,
        max_events=max_events,
    )
    feed.events = events
    feed.limitations.extend(limitations)
    feed.live = True
    return feed


# --------------------------------------------------------------------------- #
# eMarket Storage (Italy)
# --------------------------------------------------------------------------- #


def _parse_emarket_date(text: str) -> datetime | None:
    m = _EMARKET_DATE_RE.search(text)
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hour = int(m.group(4)) if m.group(4) else 0
    minute = int(m.group(5)) if m.group(5) else 0
    try:
        return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_emarket_listing(
    body: str,
    *,
    issuer_ticker: str | None,
    issuer_name: str,
    cutoff: datetime | None,
    max_events: int,
) -> tuple[list[DisclosureEvent], list[str]]:
    """Parse ONE eMarket Storage listing page. Pure; never raises.

    Each row renders as ``DD/MM/YYYY - HH:MM ISSUER <headline>`` with a link to
    the official PDF. The issuer name is stripped from the headline so the
    dedupe key matches the same announcement published elsewhere without it.
    """
    limitations: list[str] = []
    if len(body) > _MAX_RESPONSE_CHARS:
        limitations.append(_limitation("venue_response_truncated", VENUE_EMARKET_STORAGE))
        body = body[:_MAX_RESPONSE_CHARS]

    rows = _ROW_RE.findall(body)[:_MAX_ROWS_PARSED]
    if not rows:
        return [], [_limitation("venue_response_shape_unexpected", VENUE_EMARKET_STORAGE)]

    events: list[DisclosureEvent] = []
    now = _utcnow()
    for fragment in rows:
        if len(events) >= max_events:
            break
        text = _strip_tags(fragment)
        published = _parse_emarket_date(text)
        if cutoff is not None and published is not None and published < cutoff:
            continue
        headline = _EMARKET_DATE_RE.sub("", text, count=1).strip()
        # The venue prefixes every row with the issuer's SHORT name ("MONCLER
        # H1 2026 Financial Results"), not its legal name, so stripping the
        # legal name left the prefix in place — visible on a live report and,
        # worse, baked into the dedupe key, where it would stop the same
        # announcement matching a copy published without it.
        for candidate in (issuer_search_term(issuer_name), issuer_name):
            prefix = (candidate or "").strip().upper()
            if prefix and headline.upper().startswith(prefix):
                headline = headline[len(prefix) :].strip(" -–—:")
                break
        pdfs = [
            h if h.startswith("http") else f"https://{_EMARKET_HOST}{h}"
            for h in _HREF_RE.findall(fragment)
            if h.lower().endswith(".pdf")
        ]
        if not headline:
            continue
        events.append(
            DisclosureEvent(
                issuer_ticker=issuer_ticker,
                issuer_name=issuer_name,
                venue=VENUE_EMARKET_STORAGE,
                country="Italy",
                published_at=published,
                title=headline,
                category=classify_event(None, headline),
                language="it" if _looks_italian(headline) else "en",
                official_url=pdfs[0] if pdfs else None,
                attachment_urls=tuple(pdfs),
                source_tier=T2_REGULATOR_OR_GOV,
                retrieved_at=now,
                provenances=(
                    f"{VENUE_EMARKET_STORAGE} — Italian regulated-disclosure "
                    "storage mechanism",
                ),
                original_title=headline,
            )
        )
    return events, limitations


#: Italian function words. Enough to tell an Italian headline from its English
#: twin on the SAME venue (the storage mechanism publishes both), which is all
#: this is used for — never a general language classifier.
_ITALIAN_MARKERS = (
    " di ", " del ", " della ", " dei ", " degli ", " delle ", " il ", " lo ",
    " la ", " gli ", " nel ", " sulla ", "avviso", "relazione", "assemblea",
    "risultati", "bilancio", "azioni", "pubblicazione",
)


def _looks_italian(text: str) -> bool:
    lowered = f" {text.strip().lower()} "
    return any(marker in lowered for marker in _ITALIAN_MARKERS)


async def fetch_emarket_storage_disclosures(
    *,
    issuer_ticker: str | None,
    issuer_name: str,
    cfg: Settings | None = None,
    max_events: int,
    lookback_days: int,
    fetcher=safe_fetch_page,
) -> DisclosureFeed:
    """Bounded live retrieval from the Italian CONSOB-authorised storage."""
    cfg = cfg or default_settings
    feed = DisclosureFeed(venue=VENUE_EMARKET_STORAGE, retrieved_at=_utcnow())

    issuer_id = EMARKET_ISSUER_IDS.get((issuer_ticker or "").strip().upper())
    if issuer_id is None:
        feed.limitations.append(
            _limitation(
                "venue_issuer_not_registered",
                f"{issuer_ticker!r} has no curated eMarket Storage issuer id",
            )
        )
        return feed

    url = f"{_EMARKET_LIST_URL}?azienda={quote(issuer_id, safe='')}"
    result = await fetcher(
        url,
        allowed_domains=_EMARKET_ALLOWED,
        keywords=(),
        cfg=cfg,
        resolve_ip=bool(getattr(cfg, "primary_document_pin_dns_enabled", True)),
    )
    if result.blocked or result.error or not result.body_html:
        feed.limitations.append(
            _limitation("venue_unreachable", result.error or "blocked")
        )
        return feed

    cutoff = _utcnow() - timedelta(days=max(1, lookback_days))
    events, limitations = parse_emarket_listing(
        result.body_html,
        issuer_ticker=issuer_ticker,
        issuer_name=issuer_name,
        cutoff=cutoff,
        max_events=max_events,
    )
    feed.events = _english_first(events)
    feed.limitations.extend(limitations)
    if _has_bilingual_pairs(feed.events):
        # Stated rather than silently resolved: this venue publishes the SAME
        # announcement twice, once per language. Dropping the local-language
        # twin would require asserting that two differently-worded headlines
        # mean the same thing, which is a translation judgement this layer has
        # no business making. Both are kept, English first, and the reason two
        # entries appear is recorded.
        feed.limitations.append(
            _limitation(
                "venue_publishes_bilingual_pairs",
                f"{VENUE_EMARKET_STORAGE} publishes an Italian and an English "
                "edition of the same announcement; both are retained with their "
                "own official URL and language, English ordered first.",
            )
        )
    feed.live = True
    return feed


def _english_first(events: "list[DisclosureEvent]") -> "list[DisclosureEvent]":
    """Newest first, and within one timestamp the English edition first.

    Preference only — the local-language edition is never discarded, and its
    own title, URL and language are preserved exactly as published.
    """
    return sorted(
        events,
        key=lambda e: (
            -(e.published_at.timestamp() if e.published_at else 0.0),
            0 if (e.language or "").lower().startswith("en") else 1,
        ),
    )


def _has_bilingual_pairs(events: "list[DisclosureEvent]") -> bool:
    seen: dict[str, set[str]] = {}
    for event in events:
        key = event.date_key or ""
        seen.setdefault(key, set()).add((event.language or "").lower()[:2])
    return any(len(langs - {""}) > 1 for langs in seen.values())


# --------------------------------------------------------------------------- #
# Evidence adaptation — ONE place a DisclosureEvent becomes an EvidenceItem
# --------------------------------------------------------------------------- #


def disclosure_events_to_evidence(
    events: "list[DisclosureEvent]",
    *,
    source_id: str,
    transport_label: str,
    id_prefix: str,
    max_items: int,
) -> list[Any]:
    """Adapt normalized events into bounded ``EvidenceItem``s.

    ONE adapter for every venue, so an Italian and a Danish disclosure reach the
    council and the report in exactly the same shape. Each item states its own
    venue(s), publication date, category and official URL; an event that merged
    an issuer copy with an exchange copy carries BOTH provenances, so a reader
    can see it was confirmed by more than one channel.

    No item asserts materiality, direction, or any consequence for an
    investment decision — a regulated disclosure is a fact that something was
    announced, and nothing more.
    """
    from app.services.sources.evidence import build_evidence_item

    items: list[Any] = []
    for index, event in enumerate(events[:max_items], start=1):
        published = event.date_key or "date not stated"
        attachments = (
            f" Attachments: {len(event.attachment_urls)}."
            if event.attachment_urls
            else ""
        )
        excerpt = (
            f"{published} — {event.display_title()} "
            f"[{event.category}] published via {event.venue}."
            f"{attachments}"
        )
        provenance = [
            *event.provenances,
            f"published_at={published}",
            f"venue_category={event.venue_category}" if event.venue_category else "",
            f"document_identifier={event.document_identifier}"
            if event.document_identifier
            else "",
            "Regulated disclosure — a record that something was announced; "
            "no materiality, direction, or trading consequence is asserted.",
            "needs_human_review=true",
        ]
        items.append(
            build_evidence_item(
                id=f"{id_prefix}{index}",
                source_id=source_id,
                source_name=event.issuer_name or event.issuer_ticker or "Issuer",
                provider_transport=transport_label,
                provider_transport_tier=T2_REGULATOR_OR_GOV,
                content_source=event.venue,
                content_source_tier=event.source_tier,
                source_type="regulated_disclosure_event",
                title=event.display_title(),
                url=event.official_url,
                date=published,
                excerpt=excerpt,
                data_quality="B",
                confidence="high",
                requires_translation=bool(
                    event.language and not event.language.lower().startswith("en")
                ),
                original_language=event.language,
                provenance=[p for p in provenance if p],
                warnings=[
                    "Regulated disclosure retrieved from an official venue; "
                    "unverified by a human. Human review required."
                ],
            )
        )
    return items


__all__ = [
    "EMARKET_ISSUER_IDS",
    "NASDAQ_MARKETS",
    "VENUE_EMARKET_STORAGE",
    "VENUE_NASDAQ_NORDIC",
    "disclosure_events_to_evidence",
    "EMARKET_STORAGE_DOCUMENT_DOMAINS",
    "NASDAQ_NORDIC_DOCUMENT_DOMAINS",
    "fetch_emarket_storage_disclosures",
    "fetch_nasdaq_nordic_disclosures",
    "issuer_search_term",
    "parse_emarket_listing",
    "parse_nasdaq_payload",
]
