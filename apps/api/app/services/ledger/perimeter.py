"""A change of perimeter: levels from before it are not the current level — ADR-096.

WHAT WAS WRONG (QA campaign 2026-11, round 3)
=============================================
An industrial completed the spin-off of its largest segment on a stated date, and its
newest 10-Q reported debt of about USD 16.6bn and backlog of about USD 18.8bn. The chair
wrote "Total debt stood at $34.6 billion … reflecting a leveraged balance sheet" and
"Backlog as of March 31, 2026 was $38.3bn … supporting future revenue visibility": both
figures of the pre-spin-off company, presented as today's. Period supersession (ADR-087)
could not see it: the figures carried their own dates, and those dates are not old.

THE TRIGGER (conservative)
==========================
Only the issuer's OWN filings decide that the perimeter changed:

* a finding resting only on issuer documents (``issuer_filing`` / ``issuer_ir``) that
  states a COMPLETED spin-off, split-off, demerger, separation of a named business or a
  move to discontinued operations, with the date it happened ("the Aerospace Spin-Off
  completed June 29, 2026"). A planned or expected one ("expects to complete …") never
  counts, nor a date after the source's own publication;
* an SEC restatement since the annual report (``sec_edgar.basis_changes``, kind
  ``perimeter_recast``): the newest 10-Q restates a quarter the annual report filed. The
  annual figures are then on the old perimeter.

THE RULE
========
For a point-in-time LEVEL — debt / borrowings / net debt, cash, equity, backlog / order
book / remaining performance obligations — whose as-of is known (ADR-087's reading: the
period key, else a balance-date phrase, else the source date):

* as-of before the change → PRE-CHANGE. The text is labelled "[before the <date>
  separation]" (a true date: it never says what the level is now);
* as-of at or after the change but no later than the end of the quarter it happened in →
  BOUNDARY: the platform cannot tell which perimeter a balance at that date covers. Never
  labelled;
* later → POST-CHANGE. Never labelled.

A text that sets a level against another as a change ("fell from …", "down 50%",
"halved", "rose to …") where one side is PRE-CHANGE or BOUNDARY and the other POST-CHANGE
is WITHHELD: that difference is the perimeter, not deleveraging or a lost order. A
comparison the issuer's own document states is kept when the text says it is on a
comparable basis ("on a comparable basis", "excluding", "continuing operations").

WHAT IS READ
============
Findings (their ORIGINAL statement, without ADR-087 labels); the professional report's
section leads and sentences (by their F-labels); the chair's key points (by their
``finding_id``) and synthesis (by the ids a sentence prints, else — for a sentence that
cites nothing — by the subject findings whose amounts it prints, every amount matched).

Every pattern is linear and every input bounded. No issuer is named.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from app.services.ledger.canonical_state import as_date, period_bounds
from app.services.ledger.period_supersession import (
    _HEX_ID_RE,
    _SENTENCE_RE,
    MAX_TEXT_CHARS,
    _about_other_entity,
    _amounts,
    _dated_spans,
    _finding_as_of,
    _printed_amounts,
    _same_amount,
    _statement,
    _strip_ids,
    _with_label,
    balance_dates,
    families,
    text_period_keys,
)

PERIMETER_VERSION = 1

FAMILY_BACKLOG = "backlog"
LEVEL_FAMILIES = ("cash", "debt", "equity", FAMILY_BACKLOG)

CLASS_PRE = "pre_change"
CLASS_BOUNDARY = "boundary"
CLASS_POST = "post_change"

ACTION_LABELLED = "labelled_pre_change"
ACTION_WITHHELD = "withheld_cross_perimeter_comparison"

SOURCE_FINDING = "issuer_finding"
SOURCE_SEC_RECAST = "sec_restatement"

_ISSUER_KINDS = frozenset({"issuer_filing", "issuer_ir"})

#: A completed change of perimeter. Matched case-sensitively for "Separation of <Name>" /
#: "<Name> Separation" (a business), case-insensitively for the unambiguous words.
_EVENT_RE = re.compile(
    r"\b(?:spin[- ]?offs?|spun[- ]off|split[- ]off|de-?merger|demerged)\b"
    r"|\bdiscontinued\s{1,3}operations?\b",
    re.I,
)
_SEPARATION_RE = re.compile(
    r"\b[Ss]eparation\s{1,3}of\s{1,3}(?:the\s{1,3}|its\s{1,3})?[A-Z]"
    r"|\b[A-Z][a-z]{2,30}\s{1,3}Separation\b"
)
_COMPLETED_RE = re.compile(
    r"\b(?:complet(?:ed|ion\s{1,3}of)|consummated|became\s{1,3}effective|was\s{1,3}effective"
    r"|effective\s{1,3}(?:on|as\s{1,3}of)|spun[- ]off|demerged|closed)\b",
    re.I,
)
#: Planned, expected or conditional: never a completed change.
_NOT_YET_RE = re.compile(
    r"\b(?:expect\w{0,4}|anticipat\w{0,5}|plan(?:s|ned|ning)?|intend\w{0,4}|will|would"
    r"|target\w{0,4}|on\s{1,3}track|subject\s{1,3}to|proposed|pending|upon)\b",
    re.I,
)
#: Backlog-type levels. "RPO" in capitals only.
_BACKLOG_RE = re.compile(
    r"\b(?:backlogs?|order\s{1,3}books?|orderbooks?|remaining\s{1,3}performance"
    r"\s{1,3}obligations?)\b",
    re.I,
)
_RPO_RE = re.compile(r"\bRPO\b")
#: A level set against another as a change.
_CHANGE_RE = re.compile(
    r"\b(?:fell|falls?|falling|declin\w{1,4}|decreas\w{1,4}|dropp?\w{0,3}|drops?"
    r"|reduc\w{1,5}|lower(?:ed)?|rose|rises?|rising|increas\w{1,4}|grew|grows?|growth"
    r"|halved|doubled|down|up|deleverag\w{1,4}|cut|shr[aiu]nk\w{0,3}|from)\b",
    re.I,
)
#: The text itself says the comparison is like for like.
_COMPARABLE_RE = re.compile(
    r"\b(?:comparable\s{1,3}basis|like[- ]for[- ]like|continuing\s{1,3}operations"
    r"|excluding\s{1,3}the\s{1,3}(?:separated|spun|divested|discontinued)|recast|restated"
    r"|pro\s{1,3}forma|organic)\b",
    re.I,
)
_OWN_LABEL_RE = re.compile(r"\[before (?:the |a )[^\[\]]{0,200}\]\s?")
_MAX_SENTENCES = 400


def level_families(text: str) -> set[str]:
    """The point-in-time level families a text states (ADR-087's, and backlog)."""
    out = set(families(text))
    if _BACKLOG_RE.search(text) or _RPO_RE.search(text):
        out.add(FAMILY_BACKLOG)
    return out


def _own_as_ofs(text: str, today: date) -> list[date]:
    """The dates a text gives its OWN figures: balance-date phrases ("at 30 Sep 2026")
    and the ends of the periods it names ("FY2025", "Q3 2026"), none after today. Any
    other date (an event's, a maturity's) is not a level's date."""
    out = [d for d in balance_dates(text) if d <= today]
    for key in text_period_keys(text):
        bounds = period_bounds(key)
        if bounds is not None and bounds[1] <= today:
            out.append(bounds[1])
    return out


def _quarter_end(day: date) -> date:
    month = ((day.month - 1) // 3 + 1) * 3
    first_next = date(day.year + (month == 12), month % 12 + 1, 1)
    return first_next - timedelta(days=1)


def _fmt(day: date) -> str:
    return f"{day.day} {day.strftime('%b')} {day.year}"


# ── The change ────────────────────────────────────────────────────────────── #


def _issuer_only(finding: Mapping[str, Any]) -> bool:
    kinds = {str(k) for k in finding.get("source_kinds") or () if k}
    return bool(kinds) and kinds <= _ISSUER_KINDS


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    start = 0
    for m in _SENTENCE_RE.finditer(text):
        out.append(text[start: m.start()])
        start = m.end()
        if len(out) >= _MAX_SENTENCES:
            return out
    out.append(text[start:])
    return out


def change_events(
    v3: Mapping[str, Any], *, basis_changes: Any = None, as_of: date | None = None
) -> list[dict[str, Any]]:
    """Every change of perimeter the issuer's own filings state, earliest first."""
    today = as_of or date.today()
    events: dict[date, dict[str, Any]] = {}
    for finding in v3.get("findings") or ():
        if not isinstance(finding, Mapping) or not _issuer_only(finding):
            continue
        text = _statement(finding)[:MAX_TEXT_CHARS]
        ceiling = as_date(finding.get("source_published_at")) or today
        for sentence in _sentences(text):
            if not (_EVENT_RE.search(sentence) or _SEPARATION_RE.search(sentence)):
                continue
            if not _COMPLETED_RE.search(sentence) or _NOT_YET_RE.search(sentence):
                continue
            days = [d for _start, d in _dated_spans(sentence) if d <= ceiling]
            if len(set(days)) != 1:
                continue  # no date, or two: which one is the change is not certain
            day = days[0]
            events.setdefault(day, {
                "date": day.isoformat(),
                "low": day,
                "high": day,
                "source": SOURCE_FINDING,
                "finding_id": finding.get("finding_id"),
                "label": f"[before the {_fmt(day)} separation]",
            })
    for change in basis_changes or ():
        if not isinstance(change, Mapping) or change.get("kind") != "perimeter_recast":
            continue
        filed = as_date(change.get("restating_filed"))
        restated_end = as_date(change.get("period_end"))
        if filed is None or restated_end is None or events:
            continue  # a dated change from a finding is the more precise reading
        # The restated quarter, as the annual report filed it, is on the old perimeter;
        # so is the annual period itself — when the statements' latest annual period
        # plainly predates the restating 10-Q (it ended a full quarter before the 10-Q
        # was filed). Otherwise only the restated quarter is known to be old.
        annual_end = _latest_annual_end(v3)
        last_old = restated_end
        if annual_end is not None and restated_end <= annual_end <= filed - timedelta(days=92):
            last_old = annual_end
        low = last_old + timedelta(days=1)
        events.setdefault(low, {
            "date": None,
            "low": low,
            # The restating 10-Q's own period ended no earlier than 92 days before it
            # was filed; a balance after that is on the new perimeter.
            "high": max(low, filed - timedelta(days=92)),
            "source": SOURCE_SEC_RECAST,
            "restating_filed": filed.isoformat(),
            "label": (
                f"[before a change of perimeter: the issuer's 10-Q filed "
                f"{filed.isoformat()} restates earlier periods]"
            ),
        })
    return [events[k] for k in sorted(events)]


def _latest_annual_end(v3: Mapping[str, Any]) -> date | None:
    statements = v3.get("financial_statements_state")
    periods = statements.get("reporting_periods") if isinstance(statements, Mapping) else None
    latest = periods.get("latest_annual") if isinstance(periods, Mapping) else None
    bounds = period_bounds(latest) if latest else None
    return bounds[1] if bounds else None


def classify(as_of: date, event: Mapping[str, Any]) -> str:
    if as_of < event["low"]:
        return CLASS_PRE
    if as_of <= _quarter_end(event["high"]):
        return CLASS_BOUNDARY
    return CLASS_POST


# ── Texts ─────────────────────────────────────────────────────────────────── #

#: Domains whose findings are about an industry or about peers, never the subject's level.
_NOT_SUBJECT_LEVEL_DOMAINS = frozenset({"industry_economics", "competitive_position"})


def _subject_level_finding(finding: Mapping[str, Any]) -> bool:
    return (str(finding.get("domain") or "") not in _NOT_SUBJECT_LEVEL_DOMAINS
            and not _about_other_entity(finding))


class _Guard:
    def __init__(self, v3: Mapping[str, Any], event: Mapping[str, Any], today: date) -> None:
        self.event = event
        self.today = today
        self.actions: list[dict[str, Any]] = []
        self.by_id: dict[str, Mapping[str, Any]] = {}
        self.by_label: dict[str, Mapping[str, Any]] = {}
        report = v3.get("professional_research")
        labels: Mapping[str, Any] = {}
        if isinstance(report, Mapping) and isinstance(report.get("finding_labels"), Mapping):
            labels = report["finding_labels"]
        for finding in v3.get("findings") or ():
            if isinstance(finding, Mapping) and finding.get("finding_id"):
                fid = str(finding["finding_id"])
                snapshot = dict(finding)
                self.by_id.setdefault(fid, snapshot)
                label = finding.get("label") or labels.get(fid)
                if label:
                    self.by_label.setdefault(str(label), snapshot)
        self.subject = [f for f in self.by_id.values() if _subject_level_finding(f)]

    def _class_of_finding(self, finding: Mapping[str, Any]) -> str | None:
        as_of = _finding_as_of(finding, self.today)
        return classify(as_of[0], self.event) if as_of else None

    def verdict(self, text: str, backing: list[Mapping[str, Any]]) -> str | None:
        """``ACTION_LABELLED`` / ``ACTION_WITHHELD`` / None for one text unit."""
        body = _strip_ids(_OWN_LABEL_RE.sub("", text)).strip()
        if not body or len(body) > MAX_TEXT_CHARS:
            return None
        fams = level_families(body)
        if not fams:
            return None
        classes: list[str] = [classify(d, self.event) for d in _own_as_ofs(body, self.today)]
        if not classes:
            levels = [f for f in backing
                      if _subject_level_finding(f)
                      and level_families(_statement(f)) & fams
                      and _printed_amounts(_strip_ids(_statement(f)))]
            found = [self._class_of_finding(f) for f in levels]
            if not found or any(c is None for c in found):
                return None  # a level the platform cannot date: no label is certain
            classes = [c for c in found if c is not None]
        crosses = CLASS_POST in classes and (CLASS_PRE in classes or CLASS_BOUNDARY in classes)
        if crosses and _CHANGE_RE.search(body) and not _COMPARABLE_RE.search(body):
            return ACTION_WITHHELD
        if classes and all(c == CLASS_PRE for c in classes) and _printed_amounts(body):
            return ACTION_LABELLED
        return None

    def amount_backing(self, sentence: str) -> list[Mapping[str, Any]]:
        """The subject findings whose amounts a citation-free sentence prints: every
        amount it prints must be one of theirs (rounded to its printed precision)."""
        stated = _printed_amounts(_strip_ids(sentence))
        if not stated:
            return []
        backing: list[Mapping[str, Any]] = []
        for amount in stated:
            hits = [f for f in self.subject
                    if any(_same_amount(amount, b) for b in _amounts(_strip_ids(_statement(f))))]
            if not hits:
                return []  # a figure no subject level finding gives: not ours to date
            backing.extend(h for h in hits if h not in backing)
        return backing


def _labelled(text: str, label: str) -> str:
    lead = text[: len(text) - len(text.lstrip())]
    return f"{lead}{label} {text.lstrip()}"


def _notice(event: Mapping[str, Any]) -> str:
    from app.services.ledger.report_invariants import withheld_notice

    when = f"the {_fmt(event['low'])} separation" if event.get("date") else (
        "a change of perimeter the issuer's filings restate")
    return withheld_notice(
        f"it compares levels from before and after {when} as a change; the difference "
        "is the perimeter, not the business"
    )


def apply_perimeter_guard(
    v3: Mapping[str, Any], *, as_of: Any = None, basis_changes: Any = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Label pre-change levels and withhold cross-perimeter comparisons in a V3 payload.
    Works on a copy; never raises on a partial payload. Returns ``(payload, record)``."""
    out = copy.deepcopy(dict(v3))
    today = as_date(as_of) or date.today()
    events = change_events(out, basis_changes=basis_changes, as_of=today)
    record: dict[str, Any] = {
        "version": PERIMETER_VERSION,
        "as_of": today.isoformat(),
        "changes": [
            {k: (v.isoformat() if isinstance(v, date) else v) for k, v in e.items()}
            for e in events
        ],
        "actions": [],
        "note": (
            "A completed spin-off, separation, demerger or move to discontinued "
            "operations stated in the issuer's own filings (or an SEC restatement of an "
            "earlier quarter) changes the perimeter. A debt, cash, equity or backlog level "
            "from before it is labelled with that date; a text setting a level from before "
            "it against one after it as a change is withheld."
        ),
    }
    if not events:
        return out, record
    # The LATEST change governs: a level before it is on an older perimeter.
    event = events[-1]
    guard = _Guard(out, event, today)

    def handle(text: Any, backing: list[Mapping[str, Any]], path: str) -> Any:
        if not isinstance(text, str) or not text.strip() or len(text) > 20_000:
            return text
        pieces = _sentences(text)
        separators = [m.group(0) for m in _SENTENCE_RE.finditer(text)][: len(pieces) - 1]
        changed = False
        rebuilt: list[str] = []
        for i, sentence in enumerate(pieces):
            cited = backing or _cited(sentence) or guard.amount_backing(sentence)
            if _OWN_LABEL_RE.match(sentence.lstrip()):
                verdict = None
            else:
                verdict = guard.verdict(sentence, cited)
            if verdict == ACTION_LABELLED:
                sentence = _labelled(sentence, event["label"])
                changed = True
            elif verdict == ACTION_WITHHELD:
                sentence = _notice(event)
                changed = True
            if verdict:
                guard.actions.append({"action": verdict, "path": path})
            rebuilt.append(sentence + (separators[i] if i < len(separators) else ""))
        return "".join(rebuilt) if changed else text

    def _cited(sentence: str) -> list[Mapping[str, Any]]:
        found: list[Mapping[str, Any]] = []
        for m in _HEX_ID_RE.finditer(sentence):
            for fid, finding in guard.by_id.items():
                if fid.startswith(m.group(1)) and finding not in found:
                    found.append(finding)
        return found

    # Findings: one statement each, judged as one unit against itself.
    finding_actions: dict[str, str] = {}
    for fid, finding in guard.by_id.items():
        if not _subject_level_finding(finding) or _OWN_LABEL_RE.search(
                str(finding.get("statement") or "")):
            continue  # never labelled twice
        verdict = guard.verdict(_statement(finding), [finding])
        if verdict:
            finding_actions[fid] = verdict
            guard.actions.append({"action": verdict, "path": f"finding:{fid}"})

    report = out.get("professional_research")
    if isinstance(report, dict):
        for i, section in enumerate(report.get("sections") or ()):
            if not isinstance(section, dict):
                continue
            lead = section.get("lead")
            if isinstance(lead, dict):
                lead["text"] = handle(
                    lead.get("text"),
                    [guard.by_label[str(lab)] for lab in lead.get("labels") or ()
                     if str(lab) in guard.by_label],
                    f"professional_research.sections[{i}].lead")
            for j, sentence in enumerate(section.get("sentences") or ()):
                if isinstance(sentence, dict):
                    sentence["text"] = handle(
                        sentence.get("text"),
                        [guard.by_label[str(lab)] for lab in sentence.get("labels") or ()
                         if str(lab) in guard.by_label],
                        f"professional_research.sections[{i}].sentences[{j}]")
    chair = out.get("chair")
    if isinstance(chair, dict):
        chair["synthesis"] = handle(chair.get("synthesis"), [], "chair.synthesis")
        for i, point in enumerate(chair.get("key_points") or ()):
            if isinstance(point, dict):
                fid = str(point.get("finding_id") or "")
                backing = [guard.by_id[fid]] if fid in guard.by_id else []
                point["text"] = handle(point.get("text"), backing, f"chair.key_points[{i}]")

    def finding_lists() -> list[dict[str, Any]]:
        lists: list[dict[str, Any]] = [f for f in out.get("findings") or ()
                                       if isinstance(f, dict)]
        if isinstance(report, dict):
            for section in report.get("sections") or ():
                if isinstance(section, dict):
                    lists.extend(f for f in section.get("findings") or ()
                                 if isinstance(f, dict))
        return lists

    for finding in finding_lists():
        fid = str(finding.get("finding_id") or "")
        verdict = finding_actions.get(fid)
        if verdict == ACTION_LABELLED:
            # After the platform's own source labels ("[single source] …"), as ADR-087's.
            finding["statement"] = _with_label(str(finding.get("statement") or ""),
                                               event["label"])
            finding["perimeter"] = CLASS_PRE
            finding["perimeter_label"] = event["label"]
        elif verdict == ACTION_WITHHELD:
            finding["statement"] = _notice(event)
            finding["perimeter"] = "withheld"
            finding["perimeter_withheld"] = True
    record["actions"] = guard.actions
    return out, record


__all__ = [
    "ACTION_LABELLED",
    "ACTION_WITHHELD",
    "CLASS_BOUNDARY",
    "CLASS_POST",
    "CLASS_PRE",
    "FAMILY_BACKLOG",
    "PERIMETER_VERSION",
    "apply_perimeter_guard",
    "change_events",
    "classify",
    "level_families",
]
