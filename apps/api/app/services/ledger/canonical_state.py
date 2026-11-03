"""The canonical research state — ADR-082.

ONE STATE, COMPUTED ONCE
========================
The QA campaign of 2026-11 found that 16 of 16 audited reports contradicted themselves,
because every reader of a run computed its own counts and its own idea of "the latest
disclosure": the chair counted the Council's pack (empty on a refusal) and said
"0 finding(s)" against a ledger of 37; the tech block read the same field; the legacy V2
council judged its own evidence pack and said "no primary filings" while 14 findings
cited them; investigator statements named an older release as the newest document while
the 10-Q filed later was held and cited.

This module computes the run's state ONCE, from the ledger and the run's document
inventory, and every reader takes it from here:

* finding / verified / primary-source counts, gap counts by status, disagreements;
* the newest issuer document HELD (date, form, reference, period) — a cited finding's
  publication date is recorded separately and never stands in for it — and the latest
  completed reporting period the issuer's own (or verified) findings are about;
* current-period status, evidence status, chair status and council status.

It is persisted on the report as ``v3_research.canonical_state`` with a version number,
and :mod:`app.services.ledger.report_invariants` checks every sentence the report states
about the research itself against it.

PURE BY CONSTRUCTION
====================
:func:`build_canonical_state` reads plain mappings (the V3 payload, the legacy council
block) and an optional :class:`LedgerTally`. The tally is the only database read
(:func:`tally_ledger`); without it the state is derived from the payload alone and says
so (``derived_from: "payload"``), which is how the replay over stored reports runs.

No issuer is named anywhere in this module. Nothing here calls a network.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

CANONICAL_STATE_VERSION = 1

#: The issuer's OWN documents (``council_v2.inputs.ISSUER_SOURCE_KINDS``).
ISSUER_SOURCE_KINDS: frozenset[str] = frozenset({"issuer_filing", "issuer_ir"})

#: A disclosure is "current" when it is no older than this at report time.
CURRENT_WINDOW_DAYS = 120

#: Document kinds that report a period shorter than a year.
_CURRENT_PERIOD_KINDS: frozenset[str] = frozenset(
    {"interim_report", "quarterly_report", "half_year_report", "quarterly_activities"}
)

#: Fallback reasons (``agents.chair``), repeated here as plain strings so this module
#: stays importable without the agent layer.
FALLBACK_NO_MODEL = "no_chair_model_available"
FALLBACK_NOT_CONVENED = "council_did_not_convene"
FALLBACK_CHAIR_FAILED = "chair_model_failed"
FALLBACK_MATERIAL_EVENT = "synthesis_removed_by_material_event_gate"

REFUSAL_WORDS: dict[str, str] = {
    "blocking_question_open": "a blocking research question is still open",
    "gap_blocks_council": "a recorded gap blocks the council",
    "no_findings": "the run produced no evidence-linked findings",
}

#: The vendor each V3 slot prefers (``agents.routing.DEFAULT_PREFERENCES``): a slot on
#: any other vendor is a substitution, and one with none is unfilled.
_PREFERRED_VENDOR: dict[str, str] = {
    "investigator": "deepseek",
    "follow_up": "deepseek",
    "red_team": "azure_openai",
    "chair": "azure_openai",
}

_CASH_FLOW_RE = re.compile(r"\b(?:operating|free)\s+cash\s+flow|\bcash[- ]flow", re.I)
_DIGIT_RE = re.compile(r"\d")


# ---------------------------------------------------------------------------
# Periods and dates
# ---------------------------------------------------------------------------

_PERIOD_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(?:FY\s?)?(\d{4})$", re.I), "year"),
    (re.compile(r"^(\d{4})-Q([1-4])$", re.I), "quarter"),
    (re.compile(r"^Q([1-4])\s+(\d{4})$", re.I), "quarter_rev"),
    (re.compile(r"^(\d{4})-H([12])$", re.I), "half"),
    (re.compile(r"^H([12])\s+(\d{4})$", re.I), "half_rev"),
)


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def period_bounds(key: Any) -> tuple[date, date] | None:
    """``(start, end)`` of a period key such as ``FY2025``, ``2026-Q3`` or ``H1 2026``.

    Calendar approximations: a fiscal ``FY2026`` that ended in March is read as the
    calendar year. Callers clamp the end to the document's publication date, which
    removes the over-estimate in the direction that matters.
    """
    text = str(key or "").strip()
    for pattern, kind in _PERIOD_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        if kind == "year":
            year = int(match.group(1))
            return date(year, 1, 1), date(year, 12, 31)
        if kind in ("quarter", "quarter_rev"):
            year, q = (
                (int(match.group(1)), int(match.group(2)))
                if kind == "quarter"
                else (int(match.group(2)), int(match.group(1)))
            )
            return date(year, 3 * q - 2, 1), _month_end(year, 3 * q)
        year, h = (
            (int(match.group(1)), int(match.group(2)))
            if kind == "half"
            else (int(match.group(2)), int(match.group(1)))
        )
        return date(year, 6 * h - 5, 1), _month_end(year, 6 * h)
    return None


def as_date(value: Any) -> date | None:
    """A date from an ISO string, a date or a datetime; ``None`` otherwise."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if len(text) >= 10:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# The ledger tally — the one database read
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LedgerTally:
    """What the ledger records at the end of the run."""

    findings: int
    verified: int
    primary_source: int
    gaps_open: int
    gaps_by_status: dict[str, int] = field(default_factory=dict)
    disagreements: int | None = None
    unresolved_disagreement_ids: tuple[str, ...] | None = None
    #: Only when the ids are not known (a payload replay).
    unresolved_count: int | None = None

    @property
    def unresolved(self) -> int | None:
        if self.unresolved_disagreement_ids is not None:
            return len(self.unresolved_disagreement_ids)
        return self.unresolved_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": self.findings,
            "verified": self.verified,
            "primary_source": self.primary_source,
            "gaps_open": self.gaps_open,
            "gaps_by_status": dict(self.gaps_by_status),
            "disagreements": self.disagreements,
            "unresolved_disagreement_ids": (
                list(self.unresolved_disagreement_ids)
                if self.unresolved_disagreement_ids is not None
                else None
            ),
        }


async def tally_ledger(session: Any, run: Any) -> LedgerTally:
    """Count the run's ledger. Small aggregate reads only."""
    from sqlalchemy import func, select

    from app.models.ledger import ResearchDisagreement, ResearchGap
    from app.services.council_v2.inputs import ledger_counts
    from app.services.ledger import store as ledger

    counts = await ledger_counts(session, run)
    by_status: dict[str, int] = {}
    rows = (
        await session.execute(
            select(ResearchGap.status, ResearchGap.reconciliation_status, func.count())
            .where(ResearchGap.research_run_id == run.id)
            .group_by(ResearchGap.status, ResearchGap.reconciliation_status)
        )
    ).all()
    for status, reconciliation, n in rows:
        key = str(reconciliation or status)
        by_status[key] = by_status.get(key, 0) + int(n or 0)
    disagreement_rows = (
        await session.execute(
            select(ResearchDisagreement.id, ResearchDisagreement.resolution).where(
                ResearchDisagreement.research_run_id == run.id
            )
        )
    ).all()
    return LedgerTally(
        findings=counts.findings,
        verified=counts.verified,
        primary_source=counts.primary_source,
        gaps_open=counts.gaps_open,
        gaps_by_status=by_status,
        disagreements=len(disagreement_rows),
        unresolved_disagreement_ids=tuple(
            sorted(str(i) for i, res in disagreement_rows if res == ledger.UNRESOLVED)
        ),
    )


# ---------------------------------------------------------------------------
# The document inventory
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeldDocument:
    """One issuer document the run held, as the inventory records it."""

    published: date
    kind: str
    form: str | None
    ref: str | None
    period: str | None
    origin: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.published.isoformat(),
            "kind": self.kind,
            "form": self.form,
            "ref": self.ref,
            "period": self.period,
            "origin": self.origin,
        }

    def describe(self) -> str:
        what = self.form or self.kind.replace("_", " ")
        return f"{what} dated {self.published.isoformat()}"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _all_findings(v3: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Every finding the payload carries, de-duplicated by id: the ledger list (capped)
    and the professional report's sections (up to 200)."""
    seen: dict[str, Mapping[str, Any]] = {}
    for item in v3.get("findings") or ():
        if isinstance(item, Mapping):
            seen.setdefault(str(item.get("finding_id") or id(item)), item)
    report = _mapping(v3.get("professional_research"))
    for section in report.get("sections") or ():
        for item in _mapping(section).get("findings") or ():
            if isinstance(item, Mapping):
                seen.setdefault(str(item.get("finding_id") or id(item)), item)
    return list(seen.values())


def _is_issuer(finding: Mapping[str, Any]) -> bool:
    return bool(set(finding.get("source_kinds") or ()) & ISSUER_SOURCE_KINDS)


def document_inventory(v3: Mapping[str, Any]) -> list[HeldDocument]:
    """The issuer documents the run held: core filings, official disclosures, the
    statements' own documents, and the publication dates of cited issuer documents."""
    out: list[HeldDocument] = []
    filings = _mapping(v3.get("core_filings"))
    for slot, kind in (("annual", "annual_report"), ("quarterly", "interim_report")):
        entry = _mapping(filings.get(slot))
        published = as_date(entry.get("filing_date"))
        if published and entry.get("state") == "ready":
            out.append(
                HeldDocument(
                    published=published,
                    kind=kind,
                    form=str(entry.get("form") or "") or None,
                    ref=str(entry.get("accession") or "") or None,
                    period=str(entry.get("period_end") or "") or None,
                    origin="core_filings",
                )
            )
    for entry in _mapping(v3.get("core_disclosures")).get("documents") or ():
        entry = _mapping(entry)
        published = as_date(entry.get("filing_date"))
        if published and entry.get("state") == "ready":
            out.append(
                HeldDocument(
                    published=published,
                    kind=str(entry.get("document_kind") or "disclosure"),
                    form=str(entry.get("headline") or entry.get("form_type") or "") or None,
                    ref=str(entry.get("document_ref") or entry.get("source_url") or "")
                    or None,
                    period=None,
                    origin="core_disclosures",
                )
            )
    statements = _mapping(v3.get("financial_statements_state"))
    for slot in ("annual", "current_period"):
        block = _mapping(statements.get(slot))
        doc = _mapping(block.get("document"))
        published = as_date(doc.get("filing_date"))
        if published:
            out.append(
                HeldDocument(
                    published=published,
                    kind=str(doc.get("document_kind") or slot),
                    form=str(doc.get("headline") or "") or None,
                    ref=str(doc.get("source_url") or "") or None,
                    period=str(block.get("period") or "") or None,
                    origin="financial_statements",
                )
            )
    for finding in _all_findings(v3):
        published = as_date(finding.get("source_published_at"))
        if published and _is_issuer(finding):
            out.append(
                HeldDocument(
                    published=published,
                    kind="cited_issuer_document",
                    form=None,
                    ref=None,
                    period=str(finding.get("period_key") or "") or None,
                    origin="cited_finding",
                )
            )
    return out


def _latest_period(
    v3: Mapping[str, Any], as_of: date
) -> tuple[str | None, date | None, str | None]:
    """The latest COMPLETED reporting period any finding or statement is about, with its
    end and where it came from.

    Only a period whose (calendar) end is on or before its source's publication date —
    or the report date, when undated — counts. A period ending after that is either a
    forward date mistaken for a period (QA CLLS4-2: 'runway into H2 2028' tagged
    2028-H2) or a fiscal label the calendar approximation over-states; both are ignored,
    which can only make a stale-period claim LESS likely to be flagged.
    """
    best: tuple[str | None, date | None, str | None] = (None, None, None)

    def consider(key: Any, published: date | None, origin: str) -> None:
        nonlocal best
        bounds = period_bounds(key)
        if bounds is None:
            return
        end = bounds[1]
        if end > (published or as_of):
            return
        if best[1] is None or end > best[1]:
            best = (str(key), end, origin)

    statements = _mapping(v3.get("financial_statements_state"))
    periods = _mapping(statements.get("reporting_periods"))
    for slot, block_key in (
        ("latest_current_period", "current_period"),
        ("latest_annual", "annual"),
    ):
        doc = _mapping(_mapping(statements.get(block_key)).get("document"))
        consider(periods.get(slot), as_date(doc.get("filing_date")), "financial_statements")
    quarterly = _mapping(_mapping(v3.get("core_filings")).get("quarterly"))
    period_end = as_date(quarterly.get("period_end"))
    if period_end and quarterly.get("state") == "ready":
        effective = min(period_end, as_date(quarterly.get("filing_date")) or period_end)
        if best[1] is None or effective > best[1]:
            best = (period_end.isoformat(), effective, "core_filings")
    for finding in _all_findings(v3):
        # Only a finding from the issuer's own documents, or one the platform verified,
        # can establish what the latest reported period is: a secondary page about a
        # peer or a forecast is not the issuer's report (QA review N3).
        if _is_issuer(finding):
            origin = "issuer_finding"
        elif finding.get("verification_status") == "verified":
            origin = "verified_finding"
        else:
            continue
        consider(finding.get("period_key"), as_date(finding.get("source_published_at")), origin)
    return best


# ---------------------------------------------------------------------------
# The state
# ---------------------------------------------------------------------------


def _derived_tally(v3: Mapping[str, Any]) -> LedgerTally:
    """The tally from the payload alone (replays of stored reports)."""
    findings = _all_findings(v3)
    report = _mapping(v3.get("professional_research"))
    loop = _mapping(v3.get("loop"))
    council = _mapping(v3.get("council"))
    total = report.get("findings_count")
    if not isinstance(total, int):
        total = loop.get("findings") if isinstance(loop.get("findings"), int) else None
    if not isinstance(total, int):
        total = len(findings)
    gaps_open = loop.get("gaps_open")
    if not isinstance(gaps_open, int):
        gaps_open = len(
            [g for g in v3.get("gaps") or () if _mapping(g).get("status") == "open"]
        )
    # The chair's ids are populated by the PLATFORM from the ledger at chair time
    # (``agents.chair``), after the Red Team; the council block was written before it.
    # Without the ledger the larger of the two is the recorded count, and the ids
    # themselves cannot be verified (``None``).
    chair_ids = _mapping(v3.get("chair")).get("unresolved_disagreement_ids") or ()
    unresolved = max(
        int(council.get("unresolved_disagreement_count") or 0), len(list(chair_ids))
    )
    return LedgerTally(
        findings=int(total),
        verified=sum(1 for f in findings if f.get("verification_status") == "verified"),
        primary_source=sum(1 for f in findings if _is_issuer(f)),
        gaps_open=int(gaps_open),
        gaps_by_status={},
        disagreements=None,
        unresolved_disagreement_ids=None,
        unresolved_count=unresolved,
    )


_FREE_CF_RE = re.compile(r"\bfree[\s_-]+cash[\s_-]+flow|free_cash_flow", re.I)
_OPERATING_CF_RE = re.compile(r"\boperating[\s_-]+cash[\s_-]+flow|operating_cash_flow", re.I)


def _cash_flow_findings(v3: Mapping[str, Any]) -> dict[str, int]:
    """Findings and statement slots that state a cash-flow figure, by kind: ``any``,
    ``operating`` and ``free`` (an operating figure does not answer a claim about free
    cash flow, which also needs capital expenditure)."""
    counts = {"any": 0, "operating": 0, "free": 0}

    def add(text: str) -> None:
        counts["any"] += 1
        if _FREE_CF_RE.search(text):
            counts["free"] += 1
        if _OPERATING_CF_RE.search(text):
            counts["operating"] += 1

    for finding in _all_findings(v3):
        statement = str(finding.get("statement") or "")
        evidence = " ".join(str(e) for e in finding.get("evidence_ids") or ())
        if _CASH_FLOW_RE.search(statement) and _DIGIT_RE.search(statement):
            add(statement)
        elif "cash_flow" in evidence:
            add(evidence)
    slots = _mapping(_mapping(v3.get("financial_statements_state")).get("slots"))
    for key in slots:
        if "cash_flow" in str(key):
            add(str(key))
    return counts


def _current_period(v3: Mapping[str, Any]) -> dict[str, Any]:
    statements = _mapping(v3.get("financial_statements_state"))
    current = _mapping(statements.get("current_period"))
    periods = _mapping(statements.get("reporting_periods"))
    if current.get("state") == "facts_extracted" or periods.get("latest_current_period"):
        return {
            "status": "facts_validated",
            "period": periods.get("latest_current_period") or current.get("period"),
            "document": dict(_mapping(current.get("document"))) or None,
            "extraction_failed": False,
        }
    held: dict[str, Any] | None = None
    doc = _mapping(current.get("document"))
    if doc.get("filing_date"):
        held = {
            "date": str(doc.get("filing_date")),
            "form": doc.get("headline"),
            "origin": "financial_statements",
        }
    quarterly = _mapping(_mapping(v3.get("core_filings")).get("quarterly"))
    if held is None and quarterly.get("state") == "ready" and quarterly.get("filing_date"):
        held = {
            "date": str(quarterly.get("filing_date")),
            "form": quarterly.get("form"),
            "origin": "core_filings",
        }
    if held is None:
        for entry in _mapping(v3.get("core_disclosures")).get("documents") or ():
            entry = _mapping(entry)
            if entry.get("state") == "ready" and entry.get("document_kind") in (
                _CURRENT_PERIOD_KINDS
            ):
                held = {
                    "date": str(entry.get("filing_date")),
                    "form": entry.get("headline"),
                    "origin": "core_disclosures",
                }
                break
    if held is not None:
        # Held, and no statement figure was extracted from it. The current period is
        # still COVERED when an issuer-sourced finding (e.g. regulator XBRL facts) is
        # about a period ending within a reporting cycle of the document.
        held_date = as_date(held.get("date"))
        covered = False
        if held_date is not None:
            for finding in _all_findings(v3):
                bounds = period_bounds(finding.get("period_key"))
                if bounds and _is_issuer(finding) and bounds[0] <= held_date and (
                    bounds[1] >= held_date - timedelta(days=CURRENT_WINDOW_DAYS)
                ):
                    covered = True
                    break
        return {
            "status": "document_held_not_extracted",
            "period": current.get("period"),
            "document": held,
            "covered_by_issuer_findings": covered,
            # A reader cannot be told the evidence is adequate for the current period.
            "extraction_failed": not covered,
        }
    return {"status": "not_acquired", "period": None, "document": None,
            "extraction_failed": False}


def _chair(v3: Mapping[str, Any], model_available: bool | None) -> dict[str, Any]:
    chair = _mapping(v3.get("chair"))
    council = _mapping(v3.get("council"))
    if not chair:
        return {"mode": None, "fallback_reason": None, "model_available": model_available,
                "reason_text": None}
    deterministic = bool(chair.get("deterministic_fallback"))
    reason = chair.get("fallback_reason")
    if deterministic and reason is None:
        reason = FALLBACK_NO_MODEL if model_available is False else FALLBACK_CHAIR_FAILED
    if reason == FALLBACK_MATERIAL_EVENT:
        text = (
            "the chair's synthesis was replaced: it stated only material events no vetted "
            "source supports"
        )
    elif reason == FALLBACK_NOT_CONVENED or (deterministic and council.get("convened") is False):
        reason = FALLBACK_NOT_CONVENED
        why = REFUSAL_WORDS.get(str(council.get("refusal_reason")), "it was refused")
        text = f"the council did not convene because {why}"
    elif reason == FALLBACK_CHAIR_FAILED:
        text = "the chair model did not return a usable reply"
    elif reason == FALLBACK_NO_MODEL:
        text = "no chair model was available"
    else:
        text = None
    return {
        "mode": "deterministic" if deterministic else "model",
        "fallback_reason": reason if deterministic else None,
        "model_available": model_available,
        "reason_text": text,
    }


def _council(
    v3: Mapping[str, Any],
    legacy: Mapping[str, Any],
    chair: Mapping[str, Any],
    finding_total: int,
) -> dict[str, Any]:
    council = _mapping(v3.get("council"))
    routing = _mapping(_mapping(v3.get("routing")).get("slots"))
    loop = _mapping(v3.get("loop"))
    challenges = _mapping(v3.get("challenges"))

    agents = [a for a in legacy.get("agents") or () if isinstance(a, Mapping)]
    completed = legacy.get("agents_completed")
    failed = legacy.get("agents_failed")
    skipped = legacy.get("agents_skipped")
    completed = completed if isinstance(completed, int) else sum(
        1 for a in agents if a.get("status") == "completed")
    failed = failed if isinstance(failed, int) else sum(
        1 for a in agents if a.get("status") == "failed")
    skipped = skipped if isinstance(skipped, int) else 0
    requested = max(len(agents), completed + failed + skipped)

    substituted: list[dict[str, Any]] = []
    for slot, preferred in _PREFERRED_VENDOR.items():
        entry = _mapping(routing.get(slot))
        if not entry:
            continue
        vendor = entry.get("vendor")
        if vendor != preferred:
            substituted.append({"role": slot, "vendor": vendor, "preferred": preferred})

    legacy_red = next((a for a in agents if a.get("agent_name") == "red_team"), None)
    red_withheld = bool(
        legacy_red is not None
        and legacy_red.get("status") != "completed"
        and str(legacy_red.get("summary") or "").startswith("[Output withheld")
    )
    red_failed = bool(legacy_red is not None and legacy_red.get("status") == "failed")
    reviewed = challenges.get("findings_reviewed")
    if not isinstance(reviewed, int):
        reviewed = (
            int(council.get("council_finding_count", council.get("finding_count")) or 0)
            if council.get("convened")
            else 0
        )
    risk_sources = int(challenges.get("risk_evidence_items") or 0)
    reviewed_none = bool(v3.get("council")) and reviewed == 0 and risk_sources == 0

    legacy_chair_fallback = legacy.get("committee_label_basis") == "deterministic_fallback"
    reasons: list[str] = []
    if council and council.get("convened") is False:
        reasons.append(
            "council did not convene ("
            + REFUSAL_WORDS.get(str(council.get("refusal_reason")), "refused")
            + ")"
        )
    if failed:
        reasons.append(f"{failed} agent(s) failed")
    if skipped:
        reasons.append(f"{skipped} agent(s) skipped")
    if chair.get("mode") == "deterministic":
        reasons.append(
            "chair used the deterministic verdict"
            + (f" ({chair.get('reason_text')})" if chair.get("reason_text") else "")
        )
    if legacy_chair_fallback:
        reasons.append("legacy chair label is a fallback")
    if red_withheld:
        reasons.append("red team output withheld")
    elif red_failed:
        reasons.append("red team did not complete")
    if reviewed_none:
        reasons.append("red team reviewed no findings")
    for item in substituted:
        reasons.append(
            f"{item['role'].replace('_', ' ')} seat "
            + (f"on {item['vendor']}" if item["vendor"] else "unfilled")
            + f" (preferred {item['preferred']})"
        )

    degraded = bool(reasons)
    if agents or requested:
        if degraded:
            head = f"{completed} of {requested} agents completed"
        else:
            head = f"{completed} agent{'' if completed == 1 else 's'} completed"
    else:
        head = "council convened" if council.get("convened") else "council not run"
    status_text = " · ".join([head, *reasons]) if degraded else head
    return {
        "convened": council.get("convened"),
        "refusal_reason": council.get("refusal_reason"),
        "agents_requested": requested,
        "agents_completed": completed,
        "agents_failed": failed,
        "agents_skipped": skipped,
        "substituted_roles": substituted,
        "tasks_completed": loop.get("tasks_run"),
        "chair_model_available": chair.get("model_available"),
        "deterministic_fallback_used": chair.get("mode") == "deterministic",
        "legacy_chair_fallback": legacy_chair_fallback,
        "red_team": {
            "withheld": red_withheld,
            "failed": red_failed,
            "findings_reviewed": reviewed,
            "risk_sources_reviewed": risk_sources,
            "reviewed_no_sources": reviewed_none,
        },
        "findings_in_ledger": finding_total,
        "degraded": degraded,
        "degraded_reasons": reasons,
        "status_text": status_text,
    }


def build_canonical_state(
    v3: Mapping[str, Any],
    *,
    llm_council: Mapping[str, Any] | None = None,
    tally: LedgerTally | None = None,
    as_of: Any = None,
) -> dict[str, Any]:
    """The run's one research state. Pure; never raises on a partial payload."""
    v3 = _mapping(v3)
    legacy = _mapping(llm_council)
    today = as_date(as_of) or date.today()
    derived_from = "ledger" if tally is not None else "payload"
    tally = tally or _derived_tally(v3)

    inventory = document_inventory(v3)
    issuer_docs = [d for d in inventory if d.origin != "cited_finding"]
    # The newest document is one the platform HOLDS (filings, disclosures, statement
    # documents). A cited finding's publication date is not: the finding may be about a
    # different issuer the page also names (QA review N1: a spun-off subsidiary's page
    # outranked the subject's own 10-Q). Cited dates still count for "recently cited".
    newest = max(issuer_docs, key=lambda d: d.published, default=None)
    newest_cited = max(
        (d for d in inventory if d.origin == "cited_finding"),
        key=lambda d: d.published,
        default=None,
    )
    period_key, period_end, period_origin = _latest_period(v3, today)

    routing_chair = _mapping(_mapping(_mapping(v3.get("routing")).get("slots")).get("chair"))
    model_available = (
        bool(routing_chair.get("vendor")) if routing_chair else None
    )
    chair = _chair(v3, model_available)
    current = _current_period(v3)
    cash_flow = _cash_flow_findings(v3)

    window_start = today - timedelta(days=CURRENT_WINDOW_DAYS)
    recent_cited = any(
        (as_date(f.get("source_published_at")) or date.min) >= window_start
        for f in _all_findings(v3)
        if _is_issuer(f)
    ) or bool(
        issuer_docs
        and tally.primary_source > 0
        and max(d.published for d in issuer_docs) >= window_start
    )

    reasons: list[str] = []
    if tally.findings == 0:
        reasons.append("the ledger records no findings")
    if current["extraction_failed"]:
        reasons.append(
            "the current-period issuer report was acquired but no figure was extracted "
            "from it"
        )
    if tally.primary_source == 0 and tally.findings:
        reasons.append("no finding cites the issuer's own documents")
    council_block = _mapping(v3.get("council"))
    if council_block.get("convened") is False:
        reasons.append("the council did not convene")
    evidence_status = (
        "insufficient" if tally.findings == 0 else ("incomplete" if reasons else "adequate")
    )

    return {
        "version": CANONICAL_STATE_VERSION,
        "derived_from": derived_from,
        "as_of": today.isoformat(),
        "findings": {
            "total": tally.findings,
            "verified": tally.verified,
            "primary_source": tally.primary_source,
            "council_saw": council_block.get("council_finding_count"),
        },
        "gaps": {"open": tally.gaps_open, "by_status": dict(tally.gaps_by_status)},
        "disagreements": {
            "total": tally.disagreements,
            "unresolved": tally.unresolved,
            "unresolved_ids": (
                list(tally.unresolved_disagreement_ids)
                if tally.unresolved_disagreement_ids is not None
                else None
            ),
        },
        "documents": {
            "issuer_documents_held": len(issuer_docs),
            "issuer_documents_cited": tally.primary_source > 0,
            "newest_issuer_document": newest.to_dict() if newest else None,
            "newest_cited_issuer_document": newest_cited.to_dict() if newest_cited else None,
            "inventory": [d.to_dict() for d in sorted(
                issuer_docs, key=lambda d: d.published, reverse=True)[:12]],
            "latest_reporting_period": period_key,
            "latest_reporting_period_end": period_end.isoformat() if period_end else None,
            "latest_reporting_period_source": period_origin,
        },
        "current_period": current,
        "evidence": {
            "status": evidence_status,
            "reasons": reasons,
            "cash_flow_findings": cash_flow["any"],
            "cash_flow_findings_by_kind": cash_flow,
            "recent_issuer_disclosure_cited": recent_cited,
        },
        "chair": chair,
        "council": _council(v3, legacy, chair, tally.findings),
    }


def inventory_documents(state: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    """The inventory a state records (for the invariant checker)."""
    return _mapping(state.get("documents")).get("inventory") or ()


__all__ = [
    "CANONICAL_STATE_VERSION",
    "CURRENT_WINDOW_DAYS",
    "HeldDocument",
    "LedgerTally",
    "as_date",
    "build_canonical_state",
    "document_inventory",
    "inventory_documents",
    "period_bounds",
    "tally_ledger",
]
