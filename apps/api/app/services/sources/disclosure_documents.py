"""Choosing ONE current-period document out of an issuer's regulated disclosures.

Current-period acceptance. Moncler's own investor site has been serving a
maintenance page (HTTP 403 on every path) since before the readiness campaign
closed, so its final acceptance row read `— / — / 0 T1 facts` — an honest but
incomplete answer, because the same H1 2026 Financial Results the site could
not serve were already being retrieved, in full, from the Italian
CONSOB-authorised storage mechanism. The connector had the official PDF URL and
nobody ever opened it.

Retrieving it is not a secondary-source substitution. A regulated storage
mechanism holds the document the ISSUER filed, unaltered, under a statutory
obligation; it is the same primary filing reached by a different, official
transport. The distinction is preserved everywhere it matters: the transport
stays `T2_regulator_or_gov` and the venue is named on every resulting item.

This module is the pure SELECTION half — which single disclosure, if any, is
worth opening. It fetches nothing.

Fail-closed throughout. Only a RESULTS disclosure qualifies; only one that
states a current period IN ITS OWN HEADLINE; only one whose document URL is on
the venue's own registered host. Anything else yields no candidate, which the
caller reports as a precise technical reason rather than a silent absence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.sources.disclosure_events import (
    EVENT_CATEGORY_RESULTS,
    DisclosureEvent,
)
from app.services.sources.document_period import (
    DocumentPeriod,
    detect_document_period,
)
from app.services.sources.period_state import _recency_key
from app.services.sources.verified_issuer_sources import (
    host_of,
    registrable_host_allowed,
)

#: Headline wording for the announcement ABOUT a filing rather than the filing.
#: A storage mechanism publishes both — "Notice of publication of the Half-Year
#: Financial Report 2026" is a two-page notice; "H1 2026 Financial Results" is
#: the report. Both are real disclosures and both stay visible as events; only
#: the second is worth spending an ingestion slot on.
_NOTICE_MARKERS: tuple[str, ...] = (
    "notice of publication",
    "notice of availability",
    "availability of",
    "avviso di pubblicazione",
    "publication of the",
)


@dataclass(frozen=True)
class CurrentPeriodDisclosure:
    """One regulated disclosure worth ingesting as a current-period document."""

    event: DisclosureEvent
    url: str
    period: DocumentPeriod

    @property
    def venue(self) -> str:
        return self.event.venue

    @property
    def title(self) -> str:
        return self.event.display_title()


def _is_notice_about_a_filing(title: str) -> bool:
    lowered = (title or "").strip().lower()
    return any(marker in lowered for marker in _NOTICE_MARKERS)


def select_current_period_disclosure(
    events: "list[DisclosureEvent] | None",
    *,
    allowed_domains: tuple[str, ...],
) -> CurrentPeriodDisclosure | None:
    """The newest current-period RESULTS disclosure holding a fetchable document.

    Returns ``None`` — never a guess — when no disclosure qualifies. Selection
    is by the period the headline states, not by publication date: a venue
    republishes and back-fills, and "the newest period reported" is the question
    being asked. Ties are broken by publication date, then by the URL, so the
    choice is fully determinate.
    """
    candidates: list[CurrentPeriodDisclosure] = []
    for event in events or []:
        if event.category != EVENT_CATEGORY_RESULTS:
            continue
        url = event.official_url
        if not url or not isinstance(url, str):
            continue
        if not registrable_host_allowed(host_of(url), allowed_domains):
            # The URL came off an allowlisted venue page, but a page can link
            # anywhere and this is about to become a fetch target.
            continue
        title = event.display_title()
        if _is_notice_about_a_filing(title):
            continue
        period = detect_document_period(title=title, url=url)
        if not period.is_interim:
            # An annual filing reached this way is not what the current-period
            # slot needs, and the issuer's own annual report is the better
            # source for it. An undated headline is not guessed at.
            continue
        candidates.append(CurrentPeriodDisclosure(event=event, url=url, period=period))

    if not candidates:
        return None
    return max(
        candidates,
        key=lambda c: (
            _recency_key(c.period.period),
            c.event.published_at.timestamp() if c.event.published_at else 0.0,
            c.url,
        ),
    )


def has_current_period_document(artifacts: "list[Any] | None") -> bool:
    """True when the issuer's OWN documents already cover a current period.

    Consulted so the venue path is a genuine FALLBACK: when an issuer's site
    serves its own interim report, that is the better source and no venue
    document is fetched. Reads each artifact's period the same way the
    validator does, so the two can never disagree about what was ingested.
    """
    from app.services.sources.document_period import document_period_of

    for artifact in artifacts or []:
        period = document_period_of(
            title=getattr(artifact, "title", None),
            url=getattr(artifact, "source_url", None),
            extraction=getattr(artifact, "extraction", None),
        )
        if period.is_interim:
            return True
    return False


__all__ = [
    "CurrentPeriodDisclosure",
    "has_current_period_document",
    "select_current_period_disclosure",
]
