"""The issuer's own financial statements, as the V3 run found them — item 21.

THE DEFECT
==========
A UK (FCA NSM) or ASX issuer's annual and interim reports were acquired and read into
the corpus before research, and the report still said **"Latest annual: Not reported"**.
Two independent reasons:

* the V2 report — which owns ``financial_snapshot.reporting_periods`` — is assembled
  BEFORE the V3 run acquires those documents, and only ever reads SEC XBRL and curated
  company-IR evidence; nothing read the ``ExtractedFact`` rows the disclosure
  acquisition had written;
* "Not reported" was the only word the page had for three different situations.

THE THREE SITUATIONS (never merged)
===================================
``facts_extracted``                      C — validated Group statement facts exist for a
                                         period; the period is named.
``report_acquired_facts_not_extracted``  B — the annual (or interim) report IS in the
                                         corpus, but no validated Group statement fact was
                                         extracted from it. Never "not reported".
``not_acquired``                         A — nothing was acquired. Says nothing about the
                                         issuer (``knowledge_state.NOT_ACQUIRED_BY_PLATFORM``).
``not_reported_by_issuer``               A, with evidence — the issuer's OFFICIAL listing
                                         covers at least 18 months and holds no annual
                                         report or full-year results. The only state that
                                         says anything about the issuer.

THE SLOT RULES ARE THE V2 RULES
===============================
Slots are chosen by ``final_report_generator._high_confidence_facts_for`` and
``_current_period_facts_for`` — the SAME functions that fill the V2 snapshot — so a
segment or subsidiary fact can never fill a Group slot, an interim figure never takes an
annual slot, and only a high-confidence fact is presented as THE figure. Two documents
that disagree about one Group figure for one period fill no slot; the disagreement is
listed instead of resolved by document order.

Facts are read through ``company_documents_clause``: an ``ExtractedDocument`` is shared
by content hash, so ``ExtractedDocument.company_id`` alone is whoever extracted it first.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.services.knowledge_state import NOT_ACQUIRED_BY_PLATFORM, NOT_DISCLOSED_BY_ISSUER

STATE_FACTS_EXTRACTED = "facts_extracted"
STATE_ACQUIRED_NOT_EXTRACTED = "report_acquired_facts_not_extracted"
STATE_NOT_ACQUIRED = "not_acquired"
STATE_NOT_REPORTED_BY_ISSUER = "not_reported_by_issuer"

STATEMENT_STATES: frozenset[str] = frozenset(
    {
        STATE_FACTS_EXTRACTED,
        STATE_ACQUIRED_NOT_EXTRACTED,
        STATE_NOT_ACQUIRED,
        STATE_NOT_REPORTED_BY_ISSUER,
    }
)

#: How long an official listing must reach back before "no annual report in it" is
#: evidence that the issuer has not reported one: 18 months.
NOT_REPORTED_MIN_COVERAGE_DAYS = 548

#: How many facts are read. A company's statements are hundreds of rows at most; the
#: bound exists so a pathological corpus cannot make the step slow.
MAX_FACTS = 2000

_READY_STATES = frozenset({"ready", "acquired", "reused"})


# ── Reading ───────────────────────────────────────────────────────────────── #


async def load_statement_facts(session: Any, company_id: Any) -> list[dict[str, Any]]:
    """Active, validated statement facts from THIS company's documents, as fact dicts.

    The dict shape is the one the V2 slot builder reads (``field``, ``numeric_value``,
    ``period``, ``scope``, ``confidence`` bucket, ``source_url`` …) plus the document's
    own date and kind, for provenance.
    """
    from sqlalchemy import select

    from app.models.extracted_document import ExtractedDocument, ExtractedFact
    from app.services.sources.company_documents import company_documents_clause
    from app.services.sources.extracted_fact_validator import VALIDATION_VALIDATED
    from app.services.sources.fact_scope import scope_from_columns
    from app.services.sources.primary_document_extractor import _confidence_bucket
    from app.services.sources.primary_fact_parser import ISSUER_STATEMENT_FIELDS

    rows = (
        await session.execute(
            select(ExtractedFact, ExtractedDocument)
            .join(ExtractedDocument, ExtractedDocument.id == ExtractedFact.extracted_document_id)
            .where(
                company_documents_clause(company_id),
                ExtractedFact.is_active.is_(True),
                ExtractedFact.validation_status == VALIDATION_VALIDATED,
                ExtractedFact.label.in_(sorted(ISSUER_STATEMENT_FIELDS)),
                ExtractedFact.value_numeric.is_not(None),
            )
            # Newest document first: when two documents state one figure for one period
            # (an annual report and its results announcement) and agree, the newer one's
            # provenance is shown. When they disagree nothing is shown — see below.
            .order_by(
                ExtractedDocument.doc_date.desc().nulls_last(),
                ExtractedFact.label,
                ExtractedFact.period,
                ExtractedFact.id,
            )
            .limit(MAX_FACTS)
        )
    ).all()
    out: list[dict[str, Any]] = []
    for fact, document in rows:
        scope = scope_from_columns(fact.scope_type, fact.scope_name, fact.scope_key)
        out.append(
            {
                "field": fact.label,
                "value": fact.value_text,
                "numeric_value": float(fact.value_numeric),
                "unit": fact.unit,
                "currency": fact.currency,
                "scale": fact.scale,
                "period": fact.period,
                "scope": scope.label,
                "source_url": document.canonical_url,
                "page_number": fact.page_number,
                "excerpt_id": fact.table_location,
                "confidence": _confidence_bucket(float(fact.confidence or 0.0)),
                "fact_id": str(fact.id),
                "document_id": str(document.id),
                "document_title": document.title,
                "document_date": document.doc_date.isoformat() if document.doc_date else None,
                "document_source_type": document.source_type,
            }
        )
    return out


# ── Slots ─────────────────────────────────────────────────────────────────── #


def _magnitude(fact: dict[str, Any]) -> float | None:
    from app.services.sources.extracted_fact_validator import _SCALE_MULTIPLIER

    value = fact.get("numeric_value")
    if value is None:
        return None
    scale = fact.get("scale")
    if scale and scale not in _SCALE_MULTIPLIER:
        return None
    return float(value) * _SCALE_MULTIPLIER.get(scale or "", 1.0)


def _agree(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two statements of one figure agree when they are within 1% on a common scale.
    A different currency or an unknown scale is not agreement."""
    if (a.get("currency") or None) != (b.get("currency") or None):
        return False
    if (a.get("scale") or None) != (b.get("scale") or None):
        ma, mb = _magnitude(a), _magnitude(b)
        if ma is None or mb is None or not (a.get("scale") and b.get("scale")):
            return False
    else:
        ma, mb = float(a["numeric_value"]), float(b["numeric_value"])
    tolerance = max(abs(ma), abs(mb)) * 0.01
    return abs(ma - mb) <= max(tolerance, 0.5)


def _without_conflicts(
    facts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Drop every high-confidence, non-segment fact whose (field, period) another
    document states differently — and list the disagreement. Never picks a side."""
    from app.services.sources.fact_scope import parse_scope

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for fact in facts:
        if fact.get("confidence") != "high" or parse_scope(fact.get("scope")).is_segment:
            continue
        groups.setdefault((str(fact["field"]), str(fact.get("period") or "")), []).append(fact)
    conflicted: set[tuple[str, str]] = set()
    conflicts: list[dict[str, Any]] = []
    for key, group in groups.items():
        if any(not _agree(group[0], other) for other in group[1:]):
            conflicted.add(key)
            conflicts.append(
                {
                    "field": key[0],
                    "period": key[1] or None,
                    "values": [
                        {
                            "numeric_value": f.get("numeric_value"),
                            "currency": f.get("currency"),
                            "scale": f.get("scale"),
                            "document_title": f.get("document_title"),
                            "document_date": f.get("document_date"),
                            "source_url": f.get("source_url"),
                        }
                        for f in group
                    ],
                    "note": (
                        "The issuer's documents state this figure differently for the same "
                        "period and scope. No value is shown for it; neither was chosen."
                    ),
                }
            )
    kept = [
        f
        for f in facts
        if (str(f["field"]), str(f.get("period") or "")) not in conflicted
        or f.get("confidence") != "high"
    ]
    return kept, conflicts


def _datapoint(fact: dict[str, Any], *, period_basis: str | None = None) -> dict[str, Any]:
    from app.services.final_report_generator import _primary_fact_dp

    dp = _primary_fact_dp(fact)
    dp["source"] = "issuer_document"
    dp["document_title"] = fact.get("document_title")
    dp["document_date"] = fact.get("document_date")
    dp["fact_id"] = fact.get("fact_id")
    if period_basis:
        dp["period_basis"] = period_basis
    return dp


def _derived_runway(
    annual: dict[str, dict[str, Any]], current: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """``cash_runway_quarters`` over each slot set that holds all three inputs.

    The engine refuses mixed periods, scopes and currencies, and a period that burned
    no cash; a refusal is recorded too, because "runway could not be computed from these
    figures" is a finding a reader should see rather than an absence.
    """
    from decimal import Decimal, InvalidOperation

    from app.services.calculations.definitions import CASH_RUNWAY_QUARTERS
    from app.services.calculations.engine import calculate
    from app.services.calculations.quantities import Quantity
    from app.services.sources.fact_scope import parse_scope
    from app.services.sources.financial_period import parse_period

    out: list[dict[str, Any]] = []
    roles = {
        "cash": "cash_and_equivalents",
        "operating_cash_flow": "operating_cash_flow",
        "capital_expenditure": "capital_expenditure",
    }
    for basis, slots in (("annual", annual), ("current_period", current)):
        if not all(field in slots for field in roles.values()):
            continue
        inputs: dict[str, Quantity] = {}
        try:
            for role, field in roles.items():
                fact = slots[field]
                # NOT coerced to Group: the implicit-Group convention is right for a
                # rendered slot and wrong for arithmetic, so an unscoped input is left
                # for the engine to refuse (``scope_unknown``).
                scope = parse_scope(fact.get("scope"))
                inputs[role] = Quantity(
                    value=Decimal(str(fact["numeric_value"])),
                    unit=fact.get("unit") or "currency_amount",
                    currency=fact.get("currency"),
                    scale=fact.get("scale"),
                    period=parse_period(fact.get("period")),
                    scope=scope,
                    fact_id=fact.get("fact_id"),
                    label=field,
                )
        except (InvalidOperation, KeyError, TypeError, ValueError):
            continue
        outcome = calculate(CASH_RUNWAY_QUARTERS, inputs)
        record = outcome.to_dict()
        record.update(
            {
                "label": CASH_RUNWAY_QUARTERS.label,
                "basis": basis,
                "provenance": "derived",
                "interpretation": CASH_RUNWAY_QUARTERS.interpretation,
                "note": (
                    "Derived by InvestingBuddy from the issuer's own statement lines; not "
                    "an issuer figure and not a forecast."
                ),
            }
        )
        out.append(record)
    return out


# ── States ────────────────────────────────────────────────────────────────── #


def _document_view(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_kind": item.get("document_kind"),
        "headline": item.get("headline"),
        "filing_date": item.get("filing_date"),
        "source_url": item.get("source_url"),
        "source_id": item.get("source_id"),
    }


_HEADLINE_YEAR_RE = re.compile(r"\b(?:FY\s?)?((?:19|20)\d{2}(?:/\d{2})?)\b", re.IGNORECASE)


def _period_hint(headline: str | None) -> str | None:
    """The period a document's own headline names — "Annual Report 2025" → FY2025 — or
    ``None``. Only ever a label for the sentence; it never fills a slot."""
    from app.services.sources.document_period import detect_document_period
    from app.services.sources.financial_period import parse_period

    found = detect_document_period(title=headline or "")
    if found.is_known:
        return found.label()
    years = {m.group(1) for m in _HEADLINE_YEAR_RE.finditer(headline or "")}
    if len(years) != 1:
        return None
    period = parse_period(next(iter(years)))
    return None if period.is_unknown else period.label()


def _is_annual_document(item: dict[str, Any]) -> bool:
    from app.services.sources.disclosures.relevance import is_full_year_results

    kind = item.get("document_kind")
    return kind == "annual_report" or (
        kind == "results_release" and is_full_year_results(item.get("headline"))
    )


def _is_interim_document(item: dict[str, Any]) -> bool:
    kind = item.get("document_kind")
    return kind == "interim_report" or (
        kind == "results_release" and not _is_annual_document(item)
    )


def _acquired(documents: list[dict[str, Any]], predicate: Any) -> dict[str, Any] | None:
    ready = [d for d in documents if d.get("state") in _READY_STATES and predicate(d)]
    ready.sort(key=lambda d: str(d.get("filing_date") or ""), reverse=True)
    return ready[0] if ready else None


def _covers_18_months(core_disclosures: dict[str, Any], now: datetime) -> bool:
    raw = core_disclosures.get("listing_oldest")
    if not raw:
        return False
    try:
        oldest = date.fromisoformat(str(raw)[:10])
    except ValueError:
        return False
    return (now.date() - oldest) >= timedelta(days=NOT_REPORTED_MIN_COVERAGE_DAYS)


def _annual_state(
    selected_annual: list[tuple[str, dict[str, Any]]],
    *,
    core_disclosures: dict[str, Any],
    core_filings: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    from app.services.sources.financial_period import PERIOD_TYPE_ANNUAL, parse_period
    from app.services.sources.period_state import select_latest_annual

    annual_periods = [
        parse_period(fact.get("period"))
        for _field, fact in selected_annual
        if parse_period(fact.get("period")).period_type == PERIOD_TYPE_ANNUAL
    ]
    latest = select_latest_annual(annual_periods)
    if not latest.is_unknown:
        return {
            "state": STATE_FACTS_EXTRACTED,
            "period": latest.label(),
            "label": latest.label(),
            "knowledge_state": None,
            "document": None,
            "reason": None,
        }

    documents = [d for d in core_disclosures.get("documents") or [] if isinstance(d, dict)]
    document = _acquired(documents, _is_annual_document)
    sec_annual = core_filings.get("annual") if isinstance(core_filings, dict) else None
    if document is None and isinstance(sec_annual, dict) and (
        sec_annual.get("state") in _READY_STATES
    ):
        document = {
            "document_kind": "annual_report",
            "headline": sec_annual.get("form"),
            "filing_date": sec_annual.get("filing_date"),
            "source_url": None,
            "source_id": "sec_edgar",
        }
    if document is not None:
        hint = _period_hint(document.get("headline"))
        when = document.get("filing_date")
        what = f"{hint} annual report" if hint else "Annual report"
        return {
            "state": STATE_ACQUIRED_NOT_EXTRACTED,
            "period": hint,
            "label": (
                f"{what} acquired{f' ({when})' if when else ''} — figures not yet extracted"
            ),
            "knowledge_state": NOT_ACQUIRED_BY_PLATFORM,
            "document": _document_view(document),
            "reason": (
                "The report is in the research corpus, but no validated Group statement "
                "figure was extracted from it."
            ),
        }

    listed_annual = int(core_disclosures.get("annual_documents_listed") or 0)
    if (
        core_disclosures.get("source_id")
        and not core_disclosures.get("skipped")
        and listed_annual == 0
        and _covers_18_months(core_disclosures, now)
    ):
        return {
            "state": STATE_NOT_REPORTED_BY_ISSUER,
            "period": None,
            "label": "Issuer has not reported an annual report in the last 18 months",
            "knowledge_state": NOT_DISCLOSED_BY_ISSUER,
            "document": None,
            "reason": (
                f"The official listing ({core_disclosures.get('source_id')}) was read back to "
                f"{core_disclosures.get('listing_oldest')} and holds no annual report or "
                "full-year results."
            ),
        }
    return {
        "state": STATE_NOT_ACQUIRED,
        "period": None,
        "label": "No annual report acquired",
        "knowledge_state": NOT_ACQUIRED_BY_PLATFORM,
        "document": None,
        "reason": (
            core_disclosures.get("skipped")
            or (sec_annual or {}).get("reason")
            or "No annual report or full-year results document is in the research corpus."
        ),
    }


def _current_state(
    current: list[tuple[str, dict[str, Any]]],
    *,
    core_disclosures: dict[str, Any],
    core_filings: dict[str, Any],
) -> dict[str, Any]:
    from app.services.sources.financial_period import parse_period
    from app.services.sources.period_state import select_latest_current_period

    latest = select_latest_current_period(
        [parse_period(fact.get("period")) for _field, fact in current]
    )
    if not latest.is_unknown:
        return {
            "state": STATE_FACTS_EXTRACTED,
            "period": latest.label(),
            "label": latest.label(),
            "knowledge_state": None,
            "document": None,
            "reason": None,
        }
    documents = [d for d in core_disclosures.get("documents") or [] if isinstance(d, dict)]
    document = _acquired(documents, _is_interim_document)
    sec_quarterly = core_filings.get("quarterly") if isinstance(core_filings, dict) else None
    if document is None and isinstance(sec_quarterly, dict) and (
        sec_quarterly.get("state") in _READY_STATES
    ):
        document = {
            "document_kind": "interim_report",
            "headline": sec_quarterly.get("form"),
            "filing_date": sec_quarterly.get("filing_date"),
            "source_url": None,
            "source_id": "sec_edgar",
        }
    if document is not None:
        hint = _period_hint(document.get("headline"))
        when = document.get("filing_date")
        what = f"{hint} interim report" if hint else "Interim report"
        return {
            "state": STATE_ACQUIRED_NOT_EXTRACTED,
            "period": hint,
            "label": (
                f"{what} acquired{f' ({when})' if when else ''} — figures not yet extracted"
            ),
            "knowledge_state": NOT_ACQUIRED_BY_PLATFORM,
            "document": _document_view(document),
            "reason": (
                "The report is in the research corpus, but no validated Group statement "
                "figure for the current period was extracted from it."
            ),
        }
    return {
        "state": STATE_NOT_ACQUIRED,
        "period": None,
        "label": "No interim report acquired",
        "knowledge_state": NOT_ACQUIRED_BY_PLATFORM,
        "document": None,
        "reason": "No interim or quarterly report is in the research corpus.",
    }


def build_financial_statements_state(
    facts: list[dict[str, Any]],
    *,
    core_disclosures: dict[str, Any] | None = None,
    core_filings: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The statements view — slots, reporting periods, the A/B/C states. Pure."""
    from app.services.final_report_generator import (
        _current_period_facts_for,
        _high_confidence_facts_for,
    )
    from app.services.sources.period_state import build_reporting_period_state, periods_of
    from app.services.sources.primary_fact_parser import ISSUER_STATEMENT_FIELDS

    now = now or datetime.now(timezone.utc)
    disclosures = core_disclosures or {}
    filings = core_filings or {}
    usable, conflicts = _without_conflicts(facts)
    selected_annual = _high_confidence_facts_for(usable, ISSUER_STATEMENT_FIELDS)
    current = _current_period_facts_for(usable, ISSUER_STATEMENT_FIELDS)

    slots: dict[str, dict[str, Any]] = {}
    annual_by_field: dict[str, dict[str, Any]] = {}
    current_by_field: dict[str, dict[str, Any]] = {}
    for field, fact in selected_annual:
        slots[f"{field}_primary_filing"] = _datapoint(fact)
        annual_by_field[field] = fact
    for field, fact in current:
        slots[f"{field}_current_period"] = _datapoint(fact, period_basis="interim")
        current_by_field[field] = fact

    periods = build_reporting_period_state(
        periods_of([fact for _field, fact in selected_annual])
        + periods_of([fact for _field, fact in current])
    )
    return {
        "version": 1,
        "annual": _annual_state(
            selected_annual, core_disclosures=disclosures, core_filings=filings, now=now
        ),
        "current_period": _current_state(
            current, core_disclosures=disclosures, core_filings=filings
        ),
        "reporting_periods": periods.as_labels(),
        "slots": slots,
        "derived": _derived_runway(annual_by_field, current_by_field),
        "conflicts": conflicts,
        "facts_read": len(facts),
        "documents_read": len({f.get("document_id") for f in facts if f.get("document_id")}),
        "provenance": "derived",
        "source": "issuer_documents",
        "note": (
            "Read from validated statement facts in the issuer's own documents acquired "
            "for this research, under the same Group-scope, period and confidence rules "
            "as the report's financial snapshot. Figures are as printed: nothing is "
            "converted, annualised or estimated, and no EBITDA is derived."
        ),
    }


async def financial_statements_for(
    session: Any,
    company: Any,
    *,
    core_disclosures: dict[str, Any] | None,
    core_filings: dict[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read and build. Never raises: a failure is recorded on the payload."""
    try:
        facts = await load_statement_facts(session, getattr(company, "id", None))
    except Exception as exc:  # noqa: BLE001 - a statements view must not end the run
        state = build_financial_statements_state(
            [], core_disclosures=core_disclosures, core_filings=core_filings, now=now
        )
        state["error"] = f"statement facts could not be read ({type(exc).__name__})"
        return state
    return build_financial_statements_state(
        facts, core_disclosures=core_disclosures, core_filings=core_filings, now=now
    )


__all__ = [
    "NOT_REPORTED_MIN_COVERAGE_DAYS",
    "STATEMENT_STATES",
    "STATE_ACQUIRED_NOT_EXTRACTED",
    "STATE_FACTS_EXTRACTED",
    "STATE_NOT_ACQUIRED",
    "STATE_NOT_REPORTED_BY_ISSUER",
    "build_financial_statements_state",
    "financial_statements_for",
    "load_statement_facts",
]
