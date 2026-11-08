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
                # ADR-090 — the document's own tier: only an issuer or official document
                # (a T1 tier) states the issuer's statements.
                "document_source_tier": document.source_tier,
            }
        )
    return out


# ── Which facts may fill a statement slot (ADR-090) ───────────────────────── #

#: Why a fact fills no slot and enters no conflict. Recorded (counts and a bounded list
#: of field / period / reason / document title), never the fact's text.
WITHHELD_NOT_A_NUMBER = "not_a_number"
WITHHELD_NOT_AN_ISSUER_DOCUMENT = "not_an_issuer_document"
WITHHELD_FORWARD_STATEMENT = "forward_statement"
WITHHELD_ANNUAL_PERIOD_NOT_ENDED = "annual_period_not_ended"
WITHHELD_PERIOD_NOT_ENDED = "period_not_ended"

_PERIOD_REASONS = frozenset({WITHHELD_ANNUAL_PERIOD_NOT_ENDED, WITHHELD_PERIOD_NOT_ENDED})

#: How many withheld facts are listed by name in the payload (all are counted).
MAX_WITHHELD_LISTED = 40

#: How a statement prints a nil row (read as 0 by the statement parsers).
_NIL_MARKS = frozenset({"-", "–", "—", "nil", "Nil", "NIL"})

#: A figure inside a fact's as-printed value: digit groups joined by a thousands
#: separator ("4,994", "3 484", "1'250", "8.257"), an optional decimal part, an optional
#: sign or accounting brackets. Every repetition is bounded: linear on any input.
_FIGURE_TOKEN_RE = re.compile(
    r"(?<![\d.,])\(?-?\d{1,3}(?:[,.'\u00a0\u202f ]\d{3}){1,8}(?:[.,]\d{1,12})?\)?"
    r"|(?<![\d.,])\(?-?\d{1,24}(?:[.,]\d{1,12})?\)?"
)

#: ADR-080 forward cues (``date_semantics`` guidance / target / forecast rows) as they
#: occur in a fact's OWN value text ("EBIT target was also cut to $4.35bn", "sales
#: guidance of $19.8 billion", "expects revenue of …"). "estimated" is deliberately not a
#: cue: an Appendix 5B states "estimated quarters of funding" as its own item (ADR-087).
_FORWARD_FACT_RE = re.compile(
    r"\b(?:target(?:s|ed|ing)?|guidance|guid(?:e|es|ed|ing)|outlook"
    r"|expect(?:s|ed|ing|ation|ations)?|anticipat(?:e|es|ed|ing)"
    r"|forecast(?:s|ed|ing)?|projected|projections?|consensus)\b"
)


def _figure_readings(token: str) -> list[float]:
    """Every reading of an as-printed figure: "4,994" (comma groups) and "22,4" (comma
    decimal) are both tried, and the fact's own parsed number decides which it was."""
    text = token.strip().strip("()").lstrip("-")
    for blank in "'\u00a0\u202f ":
        text = text.replace(blank, "")
    out: list[float] = []
    for candidate in (text.replace(",", ""), text.replace(".", "").replace(",", ".")):
        try:
            out.append(float(candidate))
        except ValueError:
            continue
    return out


def slot_number(fact: dict[str, Any]) -> str | None:
    """The fact's value as a NUMBER, as printed ("4,994", "(1,953)", "4.35"), or ``None``.

    A slot only ever holds a parsed numeric value (ADR-090 a). A fact whose stored value
    text holds no figure equal to its parsed ``numeric_value`` — a sentence, a label, a
    different number — fills no slot. A fact with no value text shows its parsed number.
    """
    import math

    raw = fact.get("numeric_value")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    text = str(fact.get("value") or "").strip()
    if not text:
        return f"{number:,.12g}" if abs(number) >= 1000 else f"{number:.12g}"
    if number == 0 and text in _NIL_MARKS:
        # A statement's printed nil ("-" in an Appendix 5B row) is the number zero.
        return "0"
    for match in _FIGURE_TOKEN_RE.finditer(text[:500]):
        for reading in _figure_readings(match.group(0)):
            if abs(reading - abs(number)) <= max(abs(number), 1.0) * 1e-9:
                return match.group(0).strip()
    return None


def _reference_date(fact: dict[str, Any], as_of: date) -> date:
    """The date a fact's period must have ended by: its document's date when known (no
    document reports a period that had not ended when it was published), never later
    than the run date."""
    raw = str(fact.get("document_date") or "")[:10]
    try:
        published = date.fromisoformat(raw) if raw else None
    except ValueError:
        published = None
    return min(published, as_of) if published is not None else as_of


def annual_has_ended(year: int, reference: date, anchor: Any = None) -> bool:
    """Had fiscal year ``year`` ENDED by ``reference``? Fail closed (ADR-090 b).

    With the issuer's fiscal calendar (an ADR-088 ``FiscalYearAnchor``) the year ends
    where the anchor projects it, plus the 52/53-week tolerance. Without one the year is
    read as the CALENDAR year: it has ended only after 31 December. The reverse of
    ADR-088's lenient reading on purpose — there, wrongly calling a period unended would
    withhold a true statement; here, wrongly calling it ended puts a part-year figure in
    an annual slot. Missing data is acceptable; wrong data is not.
    """
    from app.services.statement_kind import FISCAL_END_TOLERANCE

    end: date | None = None
    margin = timedelta(0)
    if anchor is not None:
        try:
            end = anchor.year_end(int(year))
            margin = FISCAL_END_TOLERANCE
        except Exception:  # noqa: BLE001 - a malformed anchor means the calendar year
            end = None
    if end is None:
        end = date(int(year), 12, 31)
    return reference > end + margin


def _fiscal_label_year(fact: dict[str, Any]) -> int | None:
    """The fiscal year of an interim period the fact's OWN document is titled or
    addressed as, in the issuer's explicit fiscal labelling ("FY27 Q1 sales"), when the
    fact states a figure for that same period — else ``None``.

    The issuer has reported part of fiscal year N+1, so its fiscal year N is over: the
    one calendar fact a non-SEC issuer's own documents give (ADR-065 labels). Read with
    ADR-080's detector, which refuses a forward label ("Outlook for FY27 Q1")."""
    from app.services.sources.document_period import BASIS_FISCAL_LABEL, document_period_of
    from app.services.sources.financial_period import parse_period

    stated = parse_period(fact.get("period"))
    if not stated.is_interim or not stated.year:
        return None
    raw = str(fact.get("document_date") or "")[:10]
    try:
        published = date.fromisoformat(raw) if raw else None
    except ValueError:
        published = None
    found = document_period_of(
        title=fact.get("document_title"), url=fact.get("source_url"), extraction=None,
        as_of=published,
    )
    if not found.is_known or found.basis != BASIS_FISCAL_LABEL:
        return None
    if found.period.label() != stated.label():
        return None
    return found.period.year


#: An issuer document that IS its annual report or full-year results ("Annual Report
#: 2025", "Full year results 2025/26"), never a notice of one, its governance statement
#: or an outlook. Bounded alternations: linear.
_ANNUAL_TITLE_RE = re.compile(
    r"\bannual\s+(?:financial\s+)?report\b"
    r"|\b(?:final|full[\s-]year|annual|preliminary)\s+results\b",
    re.IGNORECASE,
)
_NOT_ANNUAL_TITLE_RE = re.compile(
    r"\bcorporate\s+governance\b|\bappendix\s+4g\b|\bnotice\s+of\b|\bletter\s+to\b"
    r"|\bproxy\b|\bsustainab|\besg\b|\bpresentation\b|\bbriefing\b|\bwebcast\b"
    r"|\bconference\s+call\b|\boutlook\b|\bguidance\b|\bresults\s+date\b|\binvitation\b",
    re.IGNORECASE,
)


#: A two-digit fiscal label ("FY26") in an annual report's title or file name.
_SHORT_FY_RE = re.compile(r"\bfy\s?(\d{2})\b", re.IGNORECASE)
_NAME_SEPARATORS_RE = re.compile(r"[-_+.]")


def _url_file_name(fact: dict[str, Any]) -> str:
    """The last path segment of the fact's source URL, as words ("fy26-annual-report.pdf"
    → "fy26 annual report pdf")."""
    from urllib.parse import unquote, urlsplit

    try:
        path = urlsplit(str(fact.get("source_url") or "")[:2000]).path
    except ValueError:
        return ""
    return _NAME_SEPARATORS_RE.sub(" ", unquote(path.rsplit("/", 1)[-1]))[:300]


def _annual_name_year(name: str) -> int | None:
    """The one fiscal year a document name says it is the annual report for, or None."""
    if not name or not _ANNUAL_TITLE_RE.search(name) or _NOT_ANNUAL_TITLE_RE.search(name):
        return None
    years = {
        int(m.group(1)[:4]) + (1 if "/" in m.group(1) else 0)
        for m in _HEADLINE_YEAR_RE.finditer(name)
    }
    years |= {2000 + int(m.group(1)) for m in _SHORT_FY_RE.finditer(name)}
    return next(iter(years)) if len(years) == 1 else None


def _annual_document_year(fact: dict[str, Any]) -> int | None:
    """The fiscal year the fact's own document says it is the annual report (or full-year
    results) FOR — one year in its title ("Annual Report 2025" → 2025, "2025/26" → 2026)
    — else ``None``. An issuer publishing its annual report for year N is the evidence
    that year N had ended, on any fiscal calendar."""
    year = None
    for name in (str(fact.get("document_title") or "")[:300], _url_file_name(fact)):
        year = _annual_name_year(name)
        if year is not None:
            break
    if year is None:
        return None
    raw = str(fact.get("document_date") or "")[:10]
    try:
        published = date.fromisoformat(raw) if raw else None
    except ValueError:
        published = None
    if published is not None and published < date(year, 1, 1):
        return None  # dated before the year it names began: not that year's report
    return year


def _ineligibility(
    fact: dict[str, Any], *, as_of: date, anchor: Any, fiscal_year_begun: int | None = None
) -> str | None:
    """Why ``fact`` may not fill a statement slot or enter a conflict, or ``None``."""
    from app.services.sources.financial_period import PERIOD_TYPE_ANNUAL, parse_period
    from app.services.statement_kind import Period, not_ended_by

    tier = fact.get("document_source_tier")
    if tier is not None and not str(tier).startswith("T1"):
        # A web article, an aggregator or a trade publication restating a figure is not
        # the issuer's statement, whatever the platform's extractor read from it.
        return WITHHELD_NOT_AN_ISSUER_DOCUMENT
    if slot_number(fact) is None:
        return WITHHELD_NOT_A_NUMBER
    if _FORWARD_FACT_RE.search(str(fact.get("value") or "")[:500].lower()):
        return WITHHELD_FORWARD_STATEMENT
    period = parse_period(fact.get("period"))
    if period.is_unknown or not period.year:
        return None
    reference = _reference_date(fact, as_of)
    if period.period_type == PERIOD_TYPE_ANNUAL:
        if fiscal_year_begun is not None and period.year < fiscal_year_begun:
            return None
        if _annual_document_year(fact) == period.year:
            return None
        if not annual_has_ended(period.year, reference, anchor):
            return WITHHELD_ANNUAL_PERIOD_NOT_ENDED
        return None
    # An interim or split-year period: refused when it demonstrably had not ended
    # (ADR-088's reading, which never calls a period unended that may have ended).
    if period.is_interim and period.ordinal:
        kind = "quarter" if period.period_type == "quarter" else "half"
        judged = Period(kind, period.year, period.ordinal)
    else:
        # A split year "2025/26" ends somewhere in its second calendar year.
        judged = Period("annual", period.year + 1, calendar=True)
        if reference < date(period.year + 1, 1, 1):
            return WITHHELD_PERIOD_NOT_ENDED
        return None
    if not_ended_by(judged, reference, anchor) is not None:
        return WITHHELD_PERIOD_NOT_ENDED
    return None


def eligible_statement_facts(
    facts: list[dict[str, Any]], *, as_of: date, anchor: Any = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(eligible, withheld)``: the facts that may fill a slot or enter a conflict, and
    one record per refused fact (field, period, reason, document title and date)."""
    eligible, withheld, _begun, _period_only = _eligible(facts, as_of=as_of, anchor=anchor)
    return eligible, withheld


def statement_slot_facts(
    facts: list[dict[str, Any]] | None, *, as_of: date, anchor: Any = None
) -> list[dict[str, Any]]:
    """The facts a canonical statement slot may take — the V2 financial snapshot's
    filter (ADR-090). Facts that are not statement figures (identity fields, employee
    counts) are judged the same way; a non-dict is dropped."""
    return eligible_statement_facts(
        [f for f in facts or [] if isinstance(f, dict)], as_of=as_of, anchor=anchor
    )[0]


def _eligible(
    facts: list[dict[str, Any]], *, as_of: date, anchor: Any = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int | None, list[dict[str, Any]]]:
    """:func:`eligible_statement_facts`, plus the latest fiscal year the issuer's own
    documents show had begun (see :func:`_fiscal_label_year`), when one does, and the
    facts withheld ONLY for their period (they may still block a slot)."""
    from app.services.sources.financial_period import PERIOD_TYPE_ANNUAL, parse_period

    # First the part-year facts: an eligible one in a document the issuer titled with
    # its own fiscal label is the evidence that an earlier fiscal year had ended.
    begun: int | None = None
    for fact in facts:
        if parse_period(fact.get("period")).period_type == PERIOD_TYPE_ANNUAL:
            continue
        if _ineligibility(fact, as_of=as_of, anchor=anchor) is not None:
            continue
        year = _fiscal_label_year(fact)
        if year is not None and (begun is None or year > begun):
            begun = year
    eligible: list[dict[str, Any]] = []
    withheld: list[dict[str, Any]] = []
    period_only: list[dict[str, Any]] = []
    for fact in facts:
        reason = _ineligibility(fact, as_of=as_of, anchor=anchor, fiscal_year_begun=begun)
        if reason is None:
            eligible.append(fact)
            continue
        if reason in _PERIOD_REASONS:
            period_only.append(fact)
        withheld.append(
            {
                "field": fact.get("field"),
                "period": fact.get("period"),
                "reason": reason,
                "document_title": fact.get("document_title"),
                "document_date": fact.get("document_date"),
                "fact_id": fact.get("fact_id"),
            }
        )
    return eligible, withheld, begun, period_only


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
        groups.setdefault((str(fact["field"]), _period_group(fact)), []).append(fact)
    conflicted: set[tuple[str, str]] = set()
    conflicts: list[dict[str, Any]] = []
    for key, group in groups.items():
        if any(not _agree(group[0], other) for other in group[1:]):
            conflicted.add(key)
            conflicts.append(
                {
                    "field": key[0],
                    "period": str(group[0].get("period") or "") or None,
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
                    "note": _conflict_note(group),
                }
            )
    kept = [
        f
        for f in facts
        if (str(f["field"]), _period_group(f)) not in conflicted
        or f.get("confidence") != "high"
    ]
    return kept, conflicts


def _period_group(fact: dict[str, Any]) -> str:
    """The period a conflict compares within: like with like (ADR-090 d). The canonical
    period — its granularity (year / half / quarter / split year) and its label — so
    "2025" and "FY2025" are one period and "2026" never meets "Q3 2026". An unparsed
    period stays its own raw text."""
    from app.services.sources.financial_period import parse_period

    raw = str(fact.get("period") or "")
    period = parse_period(raw or None)
    if period.is_unknown:
        return raw
    return f"{period.period_type}:{period.label()}"


def _tagged_concept(fact: dict[str, Any]) -> str | None:
    """The XBRL concept of a tagged figure (``ixbrl:<concept>@<context>``), else None."""
    locator = str(fact.get("excerpt_id") or "")
    if not locator.startswith("ixbrl:"):
        return None
    return locator.removeprefix("ixbrl:").split("@", 1)[0] or None


def _conflict_note(group: list[dict[str, Any]]) -> str:
    """Why one (field, period) has no value. Two different TAGGED concepts are two
    measures, not one figure stated twice, and the note says so (ADR-085 review)."""
    concepts = sorted({c for c in (_tagged_concept(f) for f in group) if c})
    if len(concepts) > 1:
        return (
            "Different measures fill this field for the same period and scope ("
            + "; ".join(concepts)
            + "). No value is shown for it; neither was chosen."
        )
    return (
        (
            "The issuer's documents state this figure differently"
            if len({f.get("document_id") for f in group}) > 1
            else "One issuer document states this figure twice, differently,"
        )
        + " for the same period and scope. No value is shown for it; "
        "neither was chosen."
    )


def _datapoint(fact: dict[str, Any], *, period_basis: str | None = None) -> dict[str, Any]:
    from app.services.final_report_generator import _primary_fact_dp

    dp = _primary_fact_dp(fact)
    # ADR-090 a — the slot holds the NUMBER as printed, never the sentence it was read
    # from. Only an eligible fact reaches here, so it has one.
    dp["value"] = slot_number(fact)
    if fact.get("document_source_tier"):
        # The document's own tier, not the V2 helper's constant.
        dp["source_tier"] = fact.get("document_source_tier")
    dp["source"] = "issuer_document"
    dp["document_title"] = fact.get("document_title")
    dp["document_date"] = fact.get("document_date")
    dp["fact_id"] = fact.get("fact_id")
    concept = _tagged_concept(fact)
    if concept:
        # ADR-085 — a figure the issuer TAGGED: its concept and the taxonomy's term for
        # it (not the filing's own caption, which is not read).
        from app.integrations.xbrl_statement_concepts import taxonomy_label

        dp["xbrl_concept"] = concept
        dp["taxonomy_label"] = taxonomy_label(concept)
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
    from app.services.sources.primary_fact_parser import FIELD_DEPOSITS

    # ADR-085 review — a filer is a deposit-taking bank only when its Group deposits
    # are reported. One bank-flavoured line (a provision, an allowance, "interest
    # income, net") is reported by industrials and biotechs too, and never decides it.
    is_bank = FIELD_DEPOSITS in annual or FIELD_DEPOSITS in current
    for basis, slots in (("annual", annual), ("current_period", current)):
        if is_bank:
            # A deposit-taking bank's statements: a cash runway (like every other
            # industrial derivation) has no reading for one. Recorded, not computed.
            out.append({
                "definition_key": CASH_RUNWAY_QUARTERS.key,
                "label": CASH_RUNWAY_QUARTERS.label,
                "status": "not_meaningful",
                "value": None,
                "basis": basis,
                "provenance": "derived",
                "detail": (
                    "Group deposits are reported; a cash runway is not a measure for a "
                    "deposit-taking bank, so none is computed"
                ),
            })
            continue
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
        # Review M2 — a developer's investing spend is mostly capitalised exploration and
        # mine development, not PP&E; a runway over PP&E alone overstates it. Those lines
        # are ADDED to the capital spend when stated for the same period, scope,
        # currency and scale; "payments for exploration" of unstated treatment may
        # already sit in operating cash flow, so with it present no runway is computed.
        capex = inputs["capital_expenditure"]
        included: list[str] = []
        refusal: str | None = None
        if "exploration_payments" in slots:
            refusal = (
                "exploration payments of unstated accounting treatment are reported; they "
                "may already be in operating cash flow, so the burn cannot be stated"
            )
        for extra in ("exploration_capitalised", "development_expenditure"):
            extra_fact = slots.get(extra)
            if extra_fact is None or refusal:
                continue
            fact = extra_fact
            base = slots["capital_expenditure"]
            same = (
                # Review round 2, H4 — from the SAME cash-flow table as the capital
                # expenditure line: never a balance-sheet asset read as spend.
                fact.get("document_id") == base.get("document_id")
                and fact.get("excerpt_id") == base.get("excerpt_id")
                and fact.get("period") == base.get("period")
                and fact.get("currency") == capex.currency
                and fact.get("scale") == capex.scale
                and parse_scope(fact.get("scope")).scope_key == (
                    capex.scope.scope_key if capex.scope else None)
            )
            if not same:
                refusal = (
                    f"{extra.replace('_', ' ')} is not stated in the same cash-flow table, "
                    "period, scope, currency and scale as capital expenditure"
                )
                continue
            capex = Quantity(
                value=abs(capex.value) + abs(Decimal(str(fact["numeric_value"]))),
                unit=capex.unit, currency=capex.currency, scale=capex.scale,
                period=capex.period, scope=capex.scope, fact_id=capex.fact_id,
                label="capital_expenditure+" + extra,
            )
            included.append(extra)
        inputs["capital_expenditure"] = capex
        outcome = calculate(CASH_RUNWAY_QUARTERS, inputs)
        record = outcome.to_dict()
        if refusal:
            record.update({"status": "refused", "value": None,
                           "refusal_reason": "burn_not_statable", "detail": refusal})
        record.update(
            {
                "label": CASH_RUNWAY_QUARTERS.label,
                "basis": basis,
                "provenance": "derived",
                "interpretation": CASH_RUNWAY_QUARTERS.interpretation,
                "capital_spend_includes": ["capital_expenditure", *included],
                "note": (
                    "Derived by InvestingBuddy from the issuer's own statement lines; not "
                    "an issuer figure and not a forecast. Capital spend = "
                    + " + ".join(["capital expenditure", *(i.replace("_", " ")
                                                           for i in included)])
                    + "."
                ),
            }
        )
        out.append(record)
    return out


def _issuer_stated(current: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """ADR-087 — the issuer's own runway figure, never raises."""
    from app.services.sources.asx_appendix_5b import issuer_stated_runway

    try:
        return issuer_stated_runway(current)
    except Exception:  # noqa: BLE001 - an additive record never costs the statements view
        return []


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
    """The listing READ at least 18 months (its window) AND reaches back that far (its
    oldest document) — the second guards a truncated or paginated listing."""
    raw = core_disclosures.get("listing_oldest")
    if not raw:
        return False
    if core_disclosures.get("listing_complete") is not True:
        return False
    window = core_disclosures.get("listing_window_days")
    if window is not None and int(window) < NOT_REPORTED_MIN_COVERAGE_DAYS:
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
    withheld_annual: int = 0,
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
            # Review L2 — acquired is not "not acquired": the report is held,
            # its figures were not extracted.
            "knowledge_state": STATE_ACQUIRED_NOT_EXTRACTED,
            "document": _document_view(document),
            "reason": (
                "The report is in the research corpus, but no validated Group statement "
                "figure was extracted from it."
            ),
        }

    if withheld_annual:
        # ADR-090 — annual figures WERE read, but the fiscal year they name could not be
        # shown to have ended by their documents' dates: held, not shown. Never "not
        # acquired".
        return {
            "state": STATE_ACQUIRED_NOT_EXTRACTED,
            "period": None,
            "label": "Annual figures held — not shown: their fiscal year had not ended",
            "knowledge_state": STATE_ACQUIRED_NOT_EXTRACTED,
            "document": None,
            "reason": (
                f"{withheld_annual} annual statement figure(s) name a fiscal year that "
                "had not ended by their documents' dates (read on the issuer's fiscal "
                "calendar where known, otherwise the calendar year); none is shown as "
                "an annual figure."
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
                f"{core_disclosures.get('listing_oldest')}: "
                f"{core_disclosures.get('listing_documents', 'its')} documents listed, "
                "0 of them an annual report, annual financial report or full-year results."
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


def _six_k_predates_annual(quarterly: dict[str, Any], core_filings: Any) -> bool:
    """A 6-K filed on or before the annual report is never the current period: a Q4
    6-K filed before the 40-F reports the same year (ADR-077). The selector already
    refuses one; this keeps a slot recorded otherwise from saying "interim"."""
    if not str(quarterly.get("form") or "").upper().startswith("6-K"):
        return False
    annual = core_filings.get("annual") if isinstance(core_filings, dict) else None
    annual_date = str((annual or {}).get("filing_date") or "")[:10]
    filed = str(quarterly.get("filing_date") or "")[:10]
    return bool(annual_date and filed and filed <= annual_date)


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
    period_end: str | None = None
    interim_only = False
    if document is None and isinstance(sec_quarterly, dict) and (
        sec_quarterly.get("state") in _READY_STATES
    ) and not _six_k_predates_annual(sec_quarterly, core_filings):
        document = {
            "document_kind": "interim_report",
            # A results 6-K (ADR-077) carries the headline of the document it was read
            # through ("… Financial Results for the Second Quarter 2026"); a 10-Q only
            # its form. The period below comes from that text, or from the issuer's
            # declared period end, or is left unnamed.
            "headline": sec_quarterly.get("title") or sec_quarterly.get("form"),
            "filing_date": sec_quarterly.get("filing_date"),
            "source_url": None,
            "source_id": "sec_edgar",
        }
        period_end = sec_quarterly.get("period_end") or None
        interim_only = bool(sec_quarterly.get("title"))
    if document is not None:
        hint = _period_hint(document.get("headline"))
        if interim_only and hint:
            # An interim document never names a fiscal YEAR: a lone "2026" in a 6-K's
            # headline is not FY2026 (ADR-077).
            from app.services.sources.financial_period import (
                PERIOD_TYPE_ANNUAL,
                parse_period,
            )

            if parse_period(hint).period_type == PERIOD_TYPE_ANNUAL:
                hint = None
        when = document.get("filing_date")
        # A quarterly activities report is not an interim (half-year) report.
        quarterly = "quarter" in str(document.get("headline") or "").lower()
        noun = "quarterly report" if quarterly else "interim report"
        what = f"{hint} {noun}" if hint else noun.capitalize()
        if not hint and period_end:
            what = f"{what} for the period ended {period_end}"
        return {
            "state": STATE_ACQUIRED_NOT_EXTRACTED,
            "period": hint,
            "label": (
                f"{what} acquired{f' ({when})' if when else ''} — figures not yet extracted"
            ),
            # Review L2 — acquired is not "not acquired": the report is held,
            # its figures were not extracted.
            "knowledge_state": STATE_ACQUIRED_NOT_EXTRACTED,
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
    fiscal_anchor: Any = None,
) -> dict[str, Any]:
    """The statements view — slots, reporting periods, the A/B/C states. Pure.

    ``fiscal_anchor`` (an ADR-088 ``FiscalYearAnchor``) is the issuer's fiscal calendar
    when known; without it an annual period is judged as the calendar year (ADR-090)."""
    from app.services.final_report_generator import (
        _current_period_facts_for,
        _high_confidence_facts_for,
    )
    from app.services.sources.period_state import build_reporting_period_state, periods_of
    from app.services.sources.primary_fact_parser import ISSUER_STATEMENT_FIELDS

    now = now or datetime.now(timezone.utc)
    disclosures = core_disclosures or {}
    filings = core_filings or {}
    # ADR-090 — only a number, from an issuer document, stating an actual for a period
    # that had ended, fills a slot or enters a conflict.
    eligible, withheld, fiscal_year_begun, period_only = _eligible(
        facts, as_of=now.date(), anchor=fiscal_anchor
    )
    # A figure withheld ONLY because its period could not be shown to have ended still
    # disagrees with an eligible figure for the same field and period: it can block that
    # slot (never fill one). Withholding it must not hand the slot to the other side.
    eligible_groups = {(str(f["field"]), _period_group(f)) for f in eligible}
    blockers = [
        f for f in period_only if (str(f["field"]), _period_group(f)) in eligible_groups
    ]
    usable, conflicts = _without_conflicts([*eligible, *blockers])
    blocker_ids = {id(f) for f in blockers}
    usable = [f for f in usable if id(f) not in blocker_ids]
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
            selected_annual, core_disclosures=disclosures, core_filings=filings, now=now,
            withheld_annual=sum(
                1 for w in withheld if w.get("reason") == WITHHELD_ANNUAL_PERIOD_NOT_ENDED
            ),
        ),
        "current_period": _current_state(
            current, core_disclosures=disclosures, core_filings=filings
        ),
        "reporting_periods": periods.as_labels(),
        "slots": slots,
        "derived": _derived_runway(annual_by_field, current_by_field),
        # ADR-087 — figures the issuer states as its own metric (an Appendix 5B's
        # estimated quarters of funding), kept apart from the platform's derivations.
        "issuer_stated": _issuer_stated(current_by_field),
        "conflicts": conflicts,
        # ADR-090 — facts read but never shown as a statement figure, and why.
        "withheld_counts": _counts(withheld),
        "withheld": withheld[:MAX_WITHHELD_LISTED],
        "fiscal_calendar": (
            "issuer_sec_statements" if fiscal_anchor is not None
            else "issuer_fiscal_labels" if fiscal_year_begun is not None
            else "calendar_year"
        ),
        "fiscal_year_begun": fiscal_year_begun,
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


def _counts(withheld: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in withheld:
        reason = str(item.get("reason"))
        out[reason] = out.get(reason, 0) + 1
    return dict(sorted(out.items()))


async def _load_fiscal_anchor(company: Any) -> Any:
    """The issuer's fiscal-year anchor from its SEC statements (ADR-088's cached, bounded
    reader), or ``None``. A seam: the test suite makes it inert, as it does every other
    live SEC lookup. Never raises."""
    from app.services import statement_kind

    try:
        return await statement_kind.load_fiscal_year_anchor(
            getattr(company, "ticker", None), getattr(company, "exchange", None)
        )
    except Exception:  # noqa: BLE001 - no calendar means the calendar-year reading
        return None


async def financial_statements_for(
    session: Any,
    company: Any,
    *,
    core_disclosures: dict[str, Any] | None,
    core_filings: dict[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read and build. Never raises: a failure is recorded on the payload.

    The read runs in a SAVEPOINT, so a database error is rolled back to it instead of
    leaving the run's transaction aborted behind a swallowed exception (review M1)."""
    anchor = await _load_fiscal_anchor(company)
    try:
        async with session.begin_nested():
            facts = await load_statement_facts(session, getattr(company, "id", None))
    except Exception as exc:  # noqa: BLE001 - a statements view must not end the run
        state = build_financial_statements_state(
            [], core_disclosures=core_disclosures, core_filings=core_filings, now=now,
            fiscal_anchor=anchor,
        )
        state["error"] = f"statement facts could not be read ({type(exc).__name__})"
        return state
    return build_financial_statements_state(
        facts, core_disclosures=core_disclosures, core_filings=core_filings, now=now,
        fiscal_anchor=anchor,
    )


__all__ = [
    "NOT_REPORTED_MIN_COVERAGE_DAYS",
    "STATEMENT_STATES",
    "STATE_ACQUIRED_NOT_EXTRACTED",
    "STATE_FACTS_EXTRACTED",
    "STATE_NOT_ACQUIRED",
    "STATE_NOT_REPORTED_BY_ISSUER",
    "annual_has_ended",
    "build_financial_statements_state",
    "eligible_statement_facts",
    "slot_number",
    "statement_slot_facts",
    "financial_statements_for",
    "load_statement_facts",
]
