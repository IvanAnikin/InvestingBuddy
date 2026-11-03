"""What one date or period MENTION means in its own sentence — ADR-080.

A document mentions many dates. Only ONE kind of mention may name the period the
document reports: a mention framed as that period ("results for H1 2026", "half-year
ended 30 June 2026", "Q3 2026 results", a heading). Every other kind — a cash runway, a
guidance year, a debt maturity, a permit deadline, a trial readout, a comparative year —
is a fact ABOUT the future or the past, and it must never become the document's period,
nor through it the period of every chunk and finding built on the document.

QA campaign 2026-11, CLLS5-1 / CLLS4-2: a strategic-update 6-K published 2026-10-28
said its cash runway reached "into H2 2028". The period detector read the first period
label in the body, stamped the filing "interim report for 2028-H2", and the
investigator then discarded the one H1 2026 net-loss statement because it cited that
filing beside the H1 2026 report — two "disagreeing" periods, one of them a forecast.

Deliberately a PURE VALUE module: it reads text somebody else already normalised and
decides one thing — the role of one mention — from a bounded window of words before and
after it. It knows nothing about documents, dates or databases.

FAIL CLOSED. A body-text mention with no reporting framing is ``UNCLASSIFIED``, and
``UNCLASSIFIED`` is not a reporting period. A title or heading is different: a document
names ITSELF there, so an un-cued period in a title or heading is its reporting period
unless a cue says otherwise.

The cue tables below are a REVIEWED vocabulary: add to them with a test, never with a
regular expression that "probably" matches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── The closed vocabulary ──────────────────────────────────────────────────── #

#: The date the document itself bears (its publication / filing date).
DOCUMENT_DATE = "document_date"
#: The period the document reports — the only role that may become its period.
REPORTING_PERIOD = "reporting_period"
#: The period a single stated VALUE is for (a figure's own column or sentence). Named
#: here so the vocabulary is closed; the fact layer assigns it, this classifier does not.
FACT_PERIOD = "fact_period"
#: When something happens: a readout, a launch, first production, a commissioning.
EVENT_DATE = "event_date"
#: A date a plan, a target or guidance points at.
GUIDANCE_TARGET_DATE = "guidance_target_date"
#: How far a forecast reaches ("cash runway into H2 2028").
FORECAST_HORIZON = "forecast_horizon"
#: A date something must happen by: a deadline, an expiry, a maturity.
DEADLINE_DATE = "deadline_date"
#: A past period named for comparison ("compared with H1 2025").
HISTORICAL_REFERENCE_DATE = "historical_reference_date"
#: No cue decided it. Fail closed: NOT a reporting period.
UNCLASSIFIED = "unclassified"

DATE_ROLES: frozenset[str] = frozenset(
    {
        DOCUMENT_DATE,
        REPORTING_PERIOD,
        FACT_PERIOD,
        EVENT_DATE,
        GUIDANCE_TARGET_DATE,
        FORECAST_HORIZON,
        DEADLINE_DATE,
        HISTORICAL_REFERENCE_DATE,
        UNCLASSIFIED,
    }
)

#: Roles that point FORWARD from the document. None of them is ever a reporting period.
FORWARD_ROLES: frozenset[str] = frozenset(
    {EVENT_DATE, GUIDANCE_TARGET_DATE, FORECAST_HORIZON, DEADLINE_DATE}
)

# ── Where the mention sits ─────────────────────────────────────────────────── #

#: The document's own title, or the address it was published at.
ROLE_TITLE = "title"
#: A heading of the document.
ROLE_HEADING = "heading"
#: Running body text.
ROLE_BODY = "body"

#: How far either side of a mention a cue may sit, in normalised characters. A cue
#: further away belongs to another clause; the window is a sentence fragment, not a page.
PREFIX_WINDOW = 60
SUFFIX_WINDOW = 40

# ── The reviewed cue tables ────────────────────────────────────────────────── #
#
# Each cue is matched on normalised text (lower case, every non-alphanumeric run a
# single space — ``document_period._normalise``) and applies on the side(s) it is listed
# for: a PREFIX cue governs what follows it ("runway into H2 2028"), a SUFFIX cue what
# precedes it ("H2 2027 readout").

_PREFIX = "prefix"
_SUFFIX = "suffix"
_BOTH = (_PREFIX, _SUFFIX)


@dataclass(frozen=True)
class _Cue:
    role: str
    pattern: str
    sides: tuple[str, ...]
    #: A strong reporting cue can outvote a farther forward cue; a weak one ("for the",
    #: "ended") cannot, because "guidance for the first quarter ending …" uses it too.
    strong: bool = True


_CUES: tuple[_Cue, ...] = (
    # Forecast horizon.
    _Cue(FORECAST_HORIZON, r"runway(?: \w+){0,3} (?:into|through|until|till|to|beyond)",
         (_PREFIX,)),
    _Cue(FORECAST_HORIZON, r"runway", (_PREFIX,)),
    _Cue(FORECAST_HORIZON,
         r"fund(?:s|ed|ing)?(?: \w+){0,3} (?:operations|requirements)"
         r"(?: \w+){0,2} (?:into|through|until|till)", (_PREFIX,)),
    _Cue(FORECAST_HORIZON, r"forecast(?:s|ed|ing)?|projected|projections?|horizon", _BOTH),
    # Guidance / target / plan.
    _Cue(GUIDANCE_TARGET_DATE, r"expect(?:s|ed|ing|ation|ations)?", _BOTH),
    _Cue(GUIDANCE_TARGET_DATE, r"anticipat(?:e|es|ed|ing)", _BOTH),
    _Cue(GUIDANCE_TARGET_DATE, r"target(?:s|ed|ing)?|guidance|guid(?:e|es|ed|ing)|outlook",
         _BOTH),
    _Cue(GUIDANCE_TARGET_DATE,
         r"plan(?:s|ned)?(?: to)?|intend(?:s|ed)?(?: to)?|aim(?:s|ed)?(?: to)?|goal"
         r"|on track|scheduled|estimated|will|is set to|are set to", (_PREFIX,)),
    _Cue(GUIDANCE_TARGET_DATE, r"planned|scheduled|estimated", (_SUFFIX,)),
    # Deadline / expiry / maturity.
    _Cue(DEADLINE_DATE, r"by|no later than|not later than|before|due(?: in| by)?",
         (_PREFIX,)),
    _Cue(DEADLINE_DATE, r"deadline", _BOTH),
    _Cue(DEADLINE_DATE, r"expir(?:e|es|ed|ing|y|ation)|matur(?:e|es|ed|ing|ity|ities)",
         _BOTH),
    # A scheduled event.
    _Cue(EVENT_DATE,
         r"read ?outs?|topline|top line|data readout|first (?:production|ore|gold|oil|gas"
         r"|concentrate|shipment|patient|revenues?|sales)|commission(?:ing|ed)?"
         r"|start ?up|launch(?:es|ed|ing)?|completion|permit(?:s|ting)?|approval"
         r"|pdufa|construction", _BOTH),
    # A comparison with a past period.
    _Cue(HISTORICAL_REFERENCE_DATE,
         r"compared (?:with|to)|versus|vs|prior year|previous year|last year|same period"
         r"|restated|since", (_PREFIX,)),
    # The date the document bears.
    _Cue(DOCUMENT_DATE, r"dated|published(?: on)?|issued(?: on)?|announced on",
         (_PREFIX,)),
    # Reporting framing — strong.
    _Cue(REPORTING_PERIOD,
         r"results?|reports?|reporting period|financial statements|accounts"
         r"|trading (?:update|statement)|activities report|interim report"
         r"|half ?year(?:ly)? (?:financial )?report|quarterly report", _BOTH),
    # Reporting framing — weak.
    _Cue(REPORTING_PERIOD, r"for the|for its|ended|ending|interim|half ?year(?:ly)?",
         (_PREFIX,), strong=False),
)

_COMPILED: tuple[tuple[_Cue, re.Pattern[str]], ...] = tuple(
    (cue, re.compile(rf"\b(?:{cue.pattern})\b")) for cue in _CUES
)


@dataclass(frozen=True)
class _Hit:
    role: str
    distance: int
    strong: bool


def _hits(before: str, after: str) -> list[_Hit]:
    hits: list[_Hit] = []
    for cue, regex in _COMPILED:
        if _PREFIX in cue.sides:
            for match in regex.finditer(before):
                hits.append(_Hit(cue.role, len(before) - match.end(), cue.strong))
        if _SUFFIX in cue.sides:
            for match in regex.finditer(after):
                hits.append(_Hit(cue.role, match.start(), cue.strong))
    return hits


def classify_date_mention(
    text: str, start: int, end: int, *, role: str = ROLE_BODY
) -> str:
    """The role of the period/date mention ``text[start:end]``. Never raises.

    ``text`` must already be normalised (``document_period._normalise``). The decision:

    1. the nearest NON-reporting cue (forward, historical, document date) wins unless a
       STRONG reporting cue sits strictly nearer — a tie is resolved forward, because
       failing closed means losing a period, never inventing one;
    2. otherwise any reporting cue makes it ``REPORTING_PERIOD``;
    3. otherwise a title or heading names the document itself (``REPORTING_PERIOD``),
       and a body mention is ``UNCLASSIFIED``.
    """
    try:
        start = max(0, int(start))
        end = max(start, int(end))
        before = text[max(0, start - PREFIX_WINDOW):start]
        after = text[end:end + SUFFIX_WINDOW]
        hits = _hits(before, after)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return UNCLASSIFIED
    other = [h for h in hits if h.role != REPORTING_PERIOD]
    strong = [h for h in hits if h.role == REPORTING_PERIOD and h.strong]
    weak = [h for h in hits if h.role == REPORTING_PERIOD and not h.strong]
    nearest_other = min(other, key=lambda h: h.distance) if other else None
    nearest_strong = min(strong, key=lambda h: h.distance) if strong else None
    if nearest_other is not None and (
        nearest_strong is None or nearest_other.distance <= nearest_strong.distance
    ):
        return nearest_other.role
    if nearest_strong is not None or weak:
        return REPORTING_PERIOD
    return REPORTING_PERIOD if role in (ROLE_TITLE, ROLE_HEADING) else UNCLASSIFIED


def is_reporting_period(text: str, start: int, end: int, *, role: str = ROLE_BODY) -> bool:
    """True only when the mention is framed as the period the document reports."""
    return classify_date_mention(text, start, end, role=role) == REPORTING_PERIOD


__all__ = [
    "DATE_ROLES",
    "DEADLINE_DATE",
    "DOCUMENT_DATE",
    "EVENT_DATE",
    "FACT_PERIOD",
    "FORECAST_HORIZON",
    "FORWARD_ROLES",
    "GUIDANCE_TARGET_DATE",
    "HISTORICAL_REFERENCE_DATE",
    "PREFIX_WINDOW",
    "REPORTING_PERIOD",
    "ROLE_BODY",
    "ROLE_HEADING",
    "ROLE_TITLE",
    "SUFFIX_WINDOW",
    "UNCLASSIFIED",
    "classify_date_mention",
    "is_reporting_period",
]
