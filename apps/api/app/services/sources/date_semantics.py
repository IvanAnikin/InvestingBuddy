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
#: Review of PR #323 — a document body's FIRST line when it is short: the headline of a
#: press release ("Third Quarter 2026 Highlights", "Acme Q3 2026 Earnings"). An un-cued
#: mention there names the document's period, as in a heading; but a non-reporting cue
#: anywhere in its window still refuses it, as in body text — it is still body text.
ROLE_HEADLINE = "headline"

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


#: How firmly a reporting cue frames a mention (``MentionReading.framing``).
#: STRONG — "results", "report", "trading update": it can outvote a farther forward cue.
FRAMING_STRONG = "strong"
#: ANCHORED — "ended", "ending": the mention is the period that ENDS on a stated date.
#: It cannot outvote a forward cue ("guidance for the first quarter ending …").
FRAMING_ANCHORED = "anchored"
#: WEAK — "for the", "interim", a measure ("revenue", "sales") or a lead verb
#: ("announced"): such words frame a period the document reports only once it is OVER.
#: The caller holds a weakly framed period to that (review of PR #323, re-review).
FRAMING_WEAK = "weak"
#: No cue at all: a title, heading or headline line naming itself.
FRAMING_DEFAULT = "default"


@dataclass(frozen=True)
class _Cue:
    role: str
    pattern: str
    sides: tuple[str, ...]
    #: For a reporting cue, how firmly it frames the mention (``FRAMING_*``). Only a
    #: STRONG cue can outvote a farther forward cue.
    framing: str = FRAMING_STRONG
    #: A NOTICE cue announces a report still to come ("to report", "invitation to",
    #: "results date"). It never decides a role — "Acme to announce Q3 2026 results on
    #: 5 November" reports a quarter that is over — but it marks the window, and the
    #: caller refuses a period that had not ENDED beside one.
    notice: bool = False

    @property
    def strong(self) -> bool:
        return self.framing == FRAMING_STRONG


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
    # Re-review of PR #323 — a forward VERB after the mention: "Q4 2026 revenue will
    # reflect the new pricing", "Sales in H2 2026 to be minimal", "revenue in H2 2026
    # will decline".
    _Cue(GUIDANCE_TARGET_DATE,
         r"will|would|should|to be|is expected to|expected to|is projected to"
         r"|is set to|are set to", (_SUFFIX,)),
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
    # Final re-review of PR #323 (CLLS4-2) — a notice of a report still to come:
    # "Acme to report Q4 2026 results", "Invitation to Q4 2026 results presentation",
    # "Financial calendar: Q4 2026 report on 5 February", "H2 2026 report to include
    # impairment", "Q4 2026 interim report date changed".
    _Cue(EVENT_DATE,
         r"to (?:report|announce|publish|release|present|host)"
         r"|will (?:report|publish|announce|release|include|be published|be released)"
         r"|(?:expects?|plans?|intends?) to report|invitation|notice of|results date"
         r"|financial calendar|conference call|webcast|date changed|to include"
         r"|expected (?:in|on)", _BOTH, notice=True),
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
    # Reporting framing — anchored to a stated end.
    _Cue(REPORTING_PERIOD, r"ended|ending", (_PREFIX,), framing=FRAMING_ANCHORED),
    # Reporting framing — weak.
    _Cue(REPORTING_PERIOD, r"for the|for its|interim|half ?year(?:ly)?",
         (_PREFIX,), framing=FRAMING_WEAK),
    # Review of PR #323 — a press release's own lead line: "today announced its third
    # quarter 2026 revenue", "reported third-quarter 2026 net sales", "posted record Q3
    # 2026 revenue", "Q3 2026 highlights", "Acme Q3 2026 Earnings", "Sales in the first
    # half of 2026 rose 8%". WEAK on purpose: "guidance for Q4 2026 revenue" names a
    # measure too, and a weak cue never outvotes a forward one.
    _Cue(REPORTING_PERIOD,
         r"earnings|net sales|sales|revenues?|turnover|highlights|update"
         r"|key figures", _BOTH, framing=FRAMING_WEAK),
    _Cue(REPORTING_PERIOD, r"announc(?:e|es|ed|ing)|reported|posted",
         (_PREFIX,), framing=FRAMING_WEAK),
)

_COMPILED: tuple[tuple[_Cue, re.Pattern[str]], ...] = tuple(
    (cue, re.compile(rf"\b(?:{cue.pattern})\b")) for cue in _CUES
)


#: Review of PR #323 — in a TITLE or HEADING a non-reporting cue decides a mention only
#: when it GOVERNS it: nothing but these words sits between them. "Outlook for Q4 2026",
#: "FY27 Q1 guidance", "Q1 2028 first production update" and "notes maturing in Q1 2031"
#: are governed; "Acme Q3 2026 revenue ahead of expectations", "Q3 2026 update ahead of
#: permitting decision" and "Interim results for the six months ended 30 June 2026 and
#: outlook" are not — a headline names its own period first and its outlook after.
#: Body text keeps the fail-closed rule: any non-reporting cue in the window refuses.
_PREFIX_CONNECTORS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "its", "our", "their", "for", "of", "in", "into", "by", "to",
        "at", "on", "during", "from", "until", "till", "through", "around", "about",
        "approximately", "as", "early", "mid", "late", "end", "start", "beginning",
        "calendar", "fiscal", "financial", "full", "year", "be", "is", "are",
        # "Company expects record Q4 2026 sales" (re-review of PR #323).
        "record",
    }
)
_SUFFIX_CONNECTORS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "its", "of", "fiscal", "financial", "full", "year",
        # A measure the forward cue is about: "Q4 2026 revenue guidance".
        "revenue", "revenues", "sales", "net", "earnings", "eps", "ebitda", "income",
        "profit", "profits", "margin", "margins", "growth", "production", "output",
        "volume", "volumes", "shipments", "deliveries", "cash", "flow", "capex",
        "dividend", "data",
    }
)


#: A clause that is NOTHING but a forward label: "Outlook", "Guidance", "Production
#: guidance", "Trading outlook", "Outlook update". Split from its period by punctuation
#: ("Outlook: Q4 2026", "Q4 2026: guidance", "Trading outlook | H2 2026") it still governs
#: it — the re-review of PR #323 found the clause split had freed those periods. A clause
#: that SAYS something about the outlook ("outlook upgraded", "guidance raised") is
#: news in its own right and does not.
_LABEL_FILLERS: frozenset[str] = frozenset(
    {"update", "updated", "trading", "operating", "operational", "business", "revised",
     "new", "current", "latest", "and"}
)
_FORWARD_LABEL_ROLES: frozenset[str] = frozenset(
    {EVENT_DATE, GUIDANCE_TARGET_DATE, FORECAST_HORIZON, DEADLINE_DATE}
)


def is_forward_label(clause: str) -> bool:
    """True when the normalised ``clause`` is only a forward cue and connector words."""
    rest = f" {clause} "
    found = False
    for cue, regex in _COMPILED:
        if cue.role in _FORWARD_LABEL_ROLES and regex.search(rest):
            found = True
            rest = regex.sub(" ", rest)
    if not found:
        return False
    allowed = _PREFIX_CONNECTORS | _SUFFIX_CONNECTORS | _LABEL_FILLERS
    return all(word in allowed for word in rest.split())


@dataclass(frozen=True)
class MentionReading:
    """A mention's role and — for a reporting period — how firmly it is framed."""

    role: str
    #: ``FRAMING_*`` for a ``REPORTING_PERIOD``; ``None`` otherwise.
    framing: str | None = None
    #: A forward or notice cue sits anywhere in the window, governing the mention or
    #: not. The caller refuses a period that had not ENDED when this is set.
    forward_nearby: bool = False


@dataclass(frozen=True)
class _Hit:
    role: str
    distance: int
    framing: str
    #: Nothing but connector words between the cue and the mention.
    governs: bool = True
    notice: bool = False

    @property
    def strong(self) -> bool:
        return self.framing == FRAMING_STRONG


def _only_connectors(gap: str, connectors: frozenset[str]) -> bool:
    return all(word in connectors for word in gap.split())


def _hits(before: str, after: str) -> list[_Hit]:
    hits: list[_Hit] = []
    for cue, regex in _COMPILED:
        if _PREFIX in cue.sides:
            for match in regex.finditer(before):
                hits.append(_Hit(
                    cue.role, len(before) - match.end(), cue.framing,
                    _only_connectors(before[match.end():], _PREFIX_CONNECTORS),
                    cue.notice,
                ))
        if _SUFFIX in cue.sides:
            for match in regex.finditer(after):
                hits.append(_Hit(
                    cue.role, match.start(), cue.framing,
                    _only_connectors(after[:match.start()], _SUFFIX_CONNECTORS),
                    cue.notice,
                ))
    return hits


def classify_date_mention(
    text: str, start: int, end: int, *, role: str = ROLE_BODY
) -> str:
    """The role of the period/date mention ``text[start:end]``. Never raises."""
    return read_date_mention(text, start, end, role=role).role


def read_date_mention(
    text: str, start: int, end: int, *, role: str = ROLE_BODY
) -> MentionReading:
    """The role of the mention ``text[start:end]`` and, for a reporting period, how
    firmly it is framed (``FRAMING_*``). Never raises.

    ``text`` must already be normalised (``document_period._normalise``). The decision:

    1. the nearest NON-reporting cue (forward, historical, document date) wins unless a
       STRONG reporting cue sits strictly nearer — a tie is resolved forward, because
       failing closed means losing a period, never inventing one;
    2. otherwise any reporting cue makes it ``REPORTING_PERIOD``;
    3. otherwise a title, heading or headline line names the document itself
       (``REPORTING_PERIOD``), and a body mention is ``UNCLASSIFIED``.

    In a title or heading only a non-reporting cue that GOVERNS the mention counts in
    step 1 (``_PREFIX_CONNECTORS`` / ``_SUFFIX_CONNECTORS``); the caller also bounds a
    title's or heading's window at its clause punctuation.
    """
    try:
        start = max(0, int(start))
        end = max(start, int(end))
        before = text[max(0, start - PREFIX_WINDOW):start]
        after = text[end:end + SUFFIX_WINDOW]
        hits = _hits(before, after)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return MentionReading(UNCLASSIFIED)
    governed_only = role in (ROLE_TITLE, ROLE_HEADING)
    forward_nearby = any(h.role in FORWARD_ROLES for h in hits)
    other = [h for h in hits
             if h.role != REPORTING_PERIOD and not h.notice
             and (h.governs or not governed_only)]
    framings = {h.framing for h in hits if h.role == REPORTING_PERIOD}
    strong = [h for h in hits if h.role == REPORTING_PERIOD and h.strong]
    nearest_other = min(other, key=lambda h: h.distance) if other else None
    nearest_strong = min(strong, key=lambda h: h.distance) if strong else None
    if nearest_other is not None and (
        nearest_strong is None or nearest_other.distance <= nearest_strong.distance
    ):
        return MentionReading(nearest_other.role)
    for framing in (FRAMING_STRONG, FRAMING_ANCHORED, FRAMING_WEAK):
        if framing in framings:
            return MentionReading(REPORTING_PERIOD, framing, forward_nearby)
    if role in (ROLE_TITLE, ROLE_HEADING, ROLE_HEADLINE):
        return MentionReading(REPORTING_PERIOD, FRAMING_DEFAULT, forward_nearby)
    return MentionReading(UNCLASSIFIED)


def is_reporting_period(text: str, start: int, end: int, *, role: str = ROLE_BODY) -> bool:
    """True only when the mention is framed as the period the document reports."""
    return classify_date_mention(text, start, end, role=role) == REPORTING_PERIOD


__all__ = [
    "DATE_ROLES",
    "DEADLINE_DATE",
    "DOCUMENT_DATE",
    "EVENT_DATE",
    "FACT_PERIOD",
    "FRAMING_ANCHORED",
    "FRAMING_DEFAULT",
    "FRAMING_STRONG",
    "FRAMING_WEAK",
    "FORECAST_HORIZON",
    "FORWARD_ROLES",
    "GUIDANCE_TARGET_DATE",
    "HISTORICAL_REFERENCE_DATE",
    "MentionReading",
    "PREFIX_WINDOW",
    "REPORTING_PERIOD",
    "ROLE_BODY",
    "ROLE_HEADING",
    "ROLE_HEADLINE",
    "ROLE_TITLE",
    "SUFFIX_WINDOW",
    "UNCLASSIFIED",
    "classify_date_mention",
    "is_forward_label",
    "is_reporting_period",
    "read_date_mention",
]
