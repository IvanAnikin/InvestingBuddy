"""Change-event classification — V3.20 slice 6.2 (spec §3.6).

Static filters converge; change does not. This module labels the issuer's HELD primary
disclosures (titles of regulatory announcements and filings the corpus already holds)
with the report schema's ``event_trigger`` vocabulary, deterministically, by keyword
rules — no model call. Every event keeps the document it came from (URL, date, tier)
and the rule that matched, so a reader can check it.

Rules are deliberately narrow:

* a title that also says the event ended or failed (terminated, cancelled, withdrawn,
  revoked, refused, …) is NOT an event — it is reported as ``negated``;
* "tariff" alone is not a customs event (a feed-in tariff is a power price) — only
  trade-remedy wording is;
* a single patent grant is not a ``patent_cluster``: ``MIN_PATENT_GRANTS`` grant
  disclosures within ``PATENT_WINDOW_DAYS`` are;
* ``insider_cluster_buy`` comes from SEC Form 4 (``integrations.sec_form4``), never
  from a title.

The source of a title-derived event is the issuer's own regulatory announcement, which
the block says — the schema prefers the regulator's / registry's own feed, and a
permit register or procurement award notice is a later, stronger source.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

RULES_VERSION = "2026-11-16.1"

#: The report schema's ``discovery_profile.event_trigger.trigger_type`` vocabulary.
TRIGGER_TYPES: tuple[str, ...] = (
    "first_offtake", "permit_granted", "insider_cluster_buy", "maturity_refinanced",
    "strategic_or_state_investor", "government_contract_award", "patent_cluster",
    "export_control_or_stockpile", "customs_flow_shift", "capacity_qualification",
)
LABELS = {
    "first_offtake": "Offtake agreement",
    "permit_granted": "Permit or licence granted",
    "insider_cluster_buy": "Insider cluster buying",
    "maturity_refinanced": "Debt maturity refinanced",
    "strategic_or_state_investor": "Strategic or state investor",
    "government_contract_award": "Government contract or award",
    "patent_cluster": "Patent cluster",
    "export_control_or_stockpile": "Export control or stockpile",
    "customs_flow_shift": "Trade remedy / customs flow",
    "capacity_qualification": "Capacity or product qualification",
}

WINDOW_DAYS = 180
PATENT_WINDOW_DAYS = 365
MIN_PATENT_GRANTS = 3

_NEGATION = re.compile(
    r"\b(terminat\w*|cancel\w*|withdraw\w*|revok\w*|revocation|refus\w*|reject\w*|"
    r"denied|denial|lapse[sd]?|suspend\w*|suspension|expir\w*|default\w*|breach\w*|"
    r"rescind\w*|not (?:been )?(?:granted|approved|awarded))\b", re.I)


@dataclass(frozen=True)
class Rule:
    trigger_type: str
    key: str
    pattern: re.Pattern[str]
    #: Also required in the title (e.g. a public body for a government award).
    require: re.Pattern[str] | None = None
    #: A title that also matches this is not this event (e.g. "qualified opinion").
    unless: re.Pattern[str] | None = None


def _r(trigger: str, key: str, pattern: str, *, require: str | None = None,
       unless: str | None = None) -> Rule:
    return Rule(trigger, key, re.compile(pattern, re.I),
                re.compile(require, re.I) if require else None,
                re.compile(unless, re.I) if unless else None)


_GRANT_WORDS = r"(?:grant(?:ed|s)?|approved|issued|received|awarded|secured|obtained)"
_PERMIT_WORDS = (r"(?:permit|licen[cs]e|consent|approval|authori[sz]ation|"
                 r"mining lease|environmental impact|planning permission)s?")
#: A corporate-action title (share issues, meetings) is not a permit.
_CORPORATE = (r"\b(shareholders?|securities|shares|quotation|meeting|resolutions?|"
              r"(?-i:AGM|EGM)|options|performance rights|dividend)\b")
#: A public body — a contract or award is a GOVERNMENT one only with one of these.
_PUBLIC_BODY = (r"\b(government|federal|ministry|department of|dept\.? of|"
                r"(?-i:DOE|DoD|DOD|NATO)|loan programs office|army|navy|air force|"
                r"public agency|state agency|grant funding)\b")

#: Order matters: the first matching rule labels the title.
RULES: tuple[Rule, ...] = (
    _r("first_offtake", "offtake", r"\boff-?take\b"),
    _r("export_control_or_stockpile", "export_control",
       r"\b(export (?:control|restriction|ban|licen[cs]\w*)s?|stockpil\w*|"
       r"critical (?:minerals?|materials?) list)\b"),
    _r("customs_flow_shift", "trade_remedy",
       r"\b(anti-?dumping|countervailing|section 232|section 301|safeguard dut\w*|"
       r"import (?:dut(?:y|ies)|tariffs?|quotas?)|customs (?:ruling|duty|duties))\b"),
    _r("government_contract_award", "government_award",
       r"\b(contracts?|award\w*|grants?|funding|loans?|task orders?)\b",
       require=_PUBLIC_BODY, unless=r"\b(option|share|equity|performance right)s? grant"),
    _r("permit_granted", "permit",
       rf"\b{_PERMIT_WORDS}\b.{{0,60}}\b{_GRANT_WORDS}\b|"
       rf"\b{_GRANT_WORDS}\b.{{0,60}}\b{_PERMIT_WORDS}\b",
       unless=_CORPORATE),
    _r("strategic_or_state_investor", "strategic_investor",
       r"\b(strategic (?:investment|investor|stake)|cornerstone investor|"
       r"sovereign wealth|state-owned investor|government (?:equity|investment|stake))\b"),
    _r("maturity_refinanced", "refinancing",
       r"\b(refinanc\w*|(?:debt|credit|loan) facilit(?:y|ies) (?:amend\w*|extend\w*)|"
       r"maturity extension|extend\w* (?:the )?maturit\w*)\b"),
    _r("capacity_qualification", "qualification",
       r"\b(qualif(?:ied|ication)|type[- ]test(?:ed|ing)?|commercial operations?|"
       r"commissioning (?:complete\w*|achieved)|first (?:production|shipment|power))\b",
       unless=r"\b(opinion|audit\w*|accountants?|institutional|purchasers?|"
              r"qualified persons?|competent persons?|qualifying)\b"),
    _r("patent_cluster", "patent_grant",
       r"\bpatents?\b.{0,40}\b(granted|issued|allowed|allowance)\b|"
       r"\b(granted|issued|grant of)\b.{0,40}\bpatents?\b"),
)


def classify_title(title: str | None) -> tuple[str, str] | None:
    """``(trigger_type, rule_key)`` for a title, ``("negated", rule_key)`` when the title
    names a trigger but also says it ended or failed, ``None`` otherwise."""
    if not title:
        return None
    text = " ".join(str(title).split())
    for rule in RULES:
        if (rule.pattern.search(text)
                and (rule.require is None or rule.require.search(text))
                and (rule.unless is None or not rule.unless.search(text))):
            if _NEGATION.search(text):
                return "negated", rule.key
            return rule.trigger_type, rule.key
    return None


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def events_from_documents(
    documents: Iterable[Mapping[str, Any]],
    *,
    today: date,
    window_days: int = WINDOW_DAYS,
) -> dict[str, Any]:
    """Classify held disclosures dated within the window. Pure.

    Returns ``{events, negated, considered, undated}``; patent grants collapse into one
    ``patent_cluster`` event only when there are ``MIN_PATENT_GRANTS`` of them within
    ``PATENT_WINDOW_DAYS``.
    """
    from app.schemas.catalyst import neutralize_forbidden_terms

    since = today - timedelta(days=max(window_days, PATENT_WINDOW_DAYS))
    recent_since = today - timedelta(days=window_days)
    events: list[dict[str, Any]] = []
    negated: list[dict[str, Any]] = []
    patents: list[dict[str, Any]] = []
    considered = undated = 0
    for doc in documents:
        published = _date(doc.get("published_at"))
        if published is None:
            undated += 1
            continue
        if published < since or published > today:
            continue
        hit = classify_title(doc.get("title"))
        if published >= recent_since:
            considered += 1
        if hit is None:
            continue
        trigger, rule = hit
        row = {
            "trigger_type": trigger,
            "label": LABELS.get(trigger),
            "date": published.isoformat(),
            "title": (neutralize_forbidden_terms(str(doc.get("title"))) or "")[:300] or None,
            "document_type": doc.get("document_type"),
            "source_url": doc.get("canonical_url"),
            "source_tier": doc.get("source_tier"),
            "source_kind": "issuer_disclosure",
            "rule": rule,
        }
        if trigger == "patent_cluster":
            patents.append(row)
        elif published < recent_since:
            continue
        elif trigger == "negated":
            negated.append(row)
        else:
            events.append(row)
    patents.sort(key=lambda r: r["date"])
    for i in range(len(patents)):
        run = [p for p in patents[i:]
               if (_date(p["date"]) - _date(patents[i]["date"])).days  # type: ignore[operator]
               <= PATENT_WINDOW_DAYS]
        if len(run) >= MIN_PATENT_GRANTS:
            events.append({
                **run[-1],
                "label": LABELS["patent_cluster"],
                "title": f"{len(run)} patent grant disclosures "
                         f"{run[0]['date']} to {run[-1]['date']}",
                "rule": f"patent_grant x{len(run)} within {PATENT_WINDOW_DAYS} days",
                "members": [{"date": p["date"], "title": p["title"],
                             "source_url": p["source_url"]} for p in run],
            })
            break
    events.sort(key=lambda r: (r["date"], r["trigger_type"]), reverse=True)
    return {"events": events, "negated": negated, "considered": considered,
            "undated": undated}


def change_events_block(
    *,
    classified: Mapping[str, Any],
    insider: Mapping[str, Any] | None,
    window_days: int = WINDOW_DAYS,
) -> dict[str, Any] | None:
    """The report block, or ``None`` when there is nothing to show."""
    clusters = list((insider or {}).get("clusters") or [])
    events = list(classified.get("events") or [])
    if not events and not clusters:
        return None
    by_type: dict[str, int] = {}
    for e in events:
        by_type[e["trigger_type"]] = by_type.get(e["trigger_type"], 0) + 1
    if clusters:
        by_type["insider_cluster_buy"] = len(clusters)
    return {
        "rules_version": RULES_VERSION,
        "window_days": window_days,
        "events": events,
        "negated": list(classified.get("negated") or []),
        "insider_clusters": clusters,
        "insider_lookup": dict(insider.get("lookup") or {}) if insider else None,
        "counts_by_type": by_type,
        "documents_considered": classified.get("considered", 0),
        "documents_undated": classified.get("undated", 0),
        "note": (
            "Change events read from the issuer's own held disclosures (titles, by rule) "
            "and from SEC Form 4 open-market purchases. A title-derived event is the "
            "issuer's announcement, not the regulator's register; an event is a reason "
            "to look, not a conclusion."
        ),
    }


__all__ = [
    "LABELS",
    "MIN_PATENT_GRANTS",
    "RULES",
    "RULES_VERSION",
    "TRIGGER_TYPES",
    "WINDOW_DAYS",
    "change_events_block",
    "classify_title",
    "events_from_documents",
]
