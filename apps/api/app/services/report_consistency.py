"""Machine-verifiable CONSISTENCY INVARIANTS for an assembled report.

Private-use production readiness, PR-F.

Every corrective slice in this codebase's history has been the same story: a
report said two incompatible things at once, a human noticed, and a targeted fix
followed. A Specialist Watchmakers figure in a Group slot. "Source the annual
report" beside a T1 revenue figure extracted from that very report. "All current
data is T6" next to a validated T1 fact. The Python literal ``None`` rendered
into a sentence. "SEC XBRL" over a Danish issuer's own PDF.

Each of those was found by reading. That does not scale, and it is not a
readiness bar.

This module turns the contradiction CLASSES into assertions that run over an
assembled report. It is deliberately structured as SEMANTIC checks against typed
sections, with text scanning used only as a secondary safeguard for the two
classes that genuinely are about rendered text (``None``/enum leakage) — a
brittle string scan would fail on wording changes and pass on real
contradictions, which is the worst of both.

It is READ-ONLY and never raises: an audit that crashes on a malformed report
tells a reader nothing. Every finding names the sections that disagree, so the
output is a starting point for a fix rather than a bare verdict.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.services.sources.fact_scope import parse_scope
from app.services.sources.financial_period import (
    PERIOD_TYPE_ANNUAL,
    parse_period,
)

# ── Invariant identifiers ────────────────────────────────────────────────── #

FACT_PRESENT_AND_MISSING = "FACT_PRESENT_AND_MISSING"
PRIMARY_SOURCE_PRESENT_BUT_ACQUISITION_GAP = (
    "PRIMARY_SOURCE_PRESENT_BUT_ACQUISITION_GAP"
)
CURRENT_PERIOD_CONTRADICTION = "CURRENT_PERIOD_CONTRADICTION"
SCOPE_CONTRADICTION = "SCOPE_CONTRADICTION"
SOURCE_TIER_CONTRADICTION = "SOURCE_TIER_CONTRADICTION"
REGULATOR_VS_ISSUER_CHANNEL_MISMATCH = "REGULATOR_VS_ISSUER_CHANNEL_MISMATCH"
DFR_FIELD_GAP_FALSE_POSITIVE = "DFR_FIELD_GAP_FALSE_POSITIVE"
NONE_LITERAL_LEAK = "NONE_LITERAL_LEAK"
ENUM_REPR_LEAK = "ENUM_REPR_LEAK"
DUPLICATE_DOCUMENT_IDENTITY = "DUPLICATE_DOCUMENT_IDENTITY"
DUPLICATE_EVENT_IDENTITY = "DUPLICATE_EVENT_IDENTITY"
HISTORICAL_AS_CURRENT = "HISTORICAL_AS_CURRENT"
INTERIM_AS_ANNUAL = "INTERIM_AS_ANNUAL"
# ── Manual-QA invariants ─────────────────────────────────────────────────── #
#: The report displays live regulated disclosures from a venue AND says that
#: venue's connector is scaffolded / not fetched / disabled.
CONNECTOR_STATE_CONTRADICTION = "CONNECTOR_STATE_CONTRADICTION"
#: The report holds validated T1 primary-filing facts AND still asks generically
#: for primary filings as though none existed.
PRIMARY_FILING_REQUIRED_CONTRADICTION = "PRIMARY_FILING_REQUIRED_CONTRADICTION"
#: A non-US issuer is told to verify itself against a US/Canadian venue that
#: does not list it, while its own venue is known.
JURISDICTION_TASK_MISMATCH = "JURISDICTION_TASK_MISMATCH"
#: A per-document count and the report-level fact count disagree with nothing
#: on the row saying they count different populations.
FACT_COUNT_SEMANTICS_MISMATCH = "FACT_COUNT_SEMANTICS_MISMATCH"

ALL_INVARIANTS: tuple[str, ...] = (
    FACT_PRESENT_AND_MISSING,
    PRIMARY_SOURCE_PRESENT_BUT_ACQUISITION_GAP,
    CURRENT_PERIOD_CONTRADICTION,
    SCOPE_CONTRADICTION,
    SOURCE_TIER_CONTRADICTION,
    REGULATOR_VS_ISSUER_CHANNEL_MISMATCH,
    DFR_FIELD_GAP_FALSE_POSITIVE,
    NONE_LITERAL_LEAK,
    ENUM_REPR_LEAK,
    DUPLICATE_DOCUMENT_IDENTITY,
    DUPLICATE_EVENT_IDENTITY,
    HISTORICAL_AS_CURRENT,
    INTERIM_AS_ANNUAL,
    CONNECTOR_STATE_CONTRADICTION,
    PRIMARY_FILING_REQUIRED_CONTRADICTION,
    JURISDICTION_TASK_MISMATCH,
    FACT_COUNT_SEMANTICS_MISMATCH,
)

SEVERITY_SERIOUS = "serious"
SEVERITY_WARNING = "warning"

#: Invariants that block private-use readiness. A ``warning`` is worth showing a
#: human but does not by itself mean the report is self-contradicting.
SERIOUS_INVARIANTS: frozenset[str] = frozenset(
    {
        FACT_PRESENT_AND_MISSING,
        PRIMARY_SOURCE_PRESENT_BUT_ACQUISITION_GAP,
        CURRENT_PERIOD_CONTRADICTION,
        SCOPE_CONTRADICTION,
        SOURCE_TIER_CONTRADICTION,
        REGULATOR_VS_ISSUER_CHANNEL_MISMATCH,
        DFR_FIELD_GAP_FALSE_POSITIVE,
        NONE_LITERAL_LEAK,
        ENUM_REPR_LEAK,
        HISTORICAL_AS_CURRENT,
        INTERIM_AS_ANNUAL,
        CONNECTOR_STATE_CONTRADICTION,
        PRIMARY_FILING_REQUIRED_CONTRADICTION,
        JURISDICTION_TASK_MISMATCH,
        FACT_COUNT_SEMANTICS_MISMATCH,
    }
)

# ── Text-layer patterns (secondary safeguard only) ───────────────────────── #

#: A bare ``None`` as a WORD in rendered prose. Word-bounded so "NoneSuch" and a
#: legitimate "none of the above" are not flagged.
_NONE_LITERAL_RE = re.compile(r"(?<![A-Za-z0-9_])None(?![A-Za-z0-9_])")
#: A Python enum repr that escaped into text: ``SourceTier.T1_PRIMARY_FILING``,
#: ``GapType.primary_filing_unavailable``, ``<CatalystCategory.results: ...>``.
_ENUM_REPR_RE = re.compile(
    r"(?:<\s*)?\b(?:[A-Z][A-Za-z0-9]*)(?:Tier|Type|Status|Category|Enum|Severity|Label)"
    r"\.[A-Za-z_][A-Za-z0-9_]*"
)
#: US filing vocabulary that must not appear for a non-US issuer.
_US_FILING_RE = re.compile(r"\b(?:10-K|10-Q|40-F|8-K|SEC EDGAR|SEC XBRL)\b", re.I)
#: Keys whose VALUE is legitimately a machine identifier or a deliberate
#: notice, and which must not be scanned as human-facing prose.
_NON_PROSE_KEYS: frozenset[str] = frozenset(
    {
        "source_url",
        "url",
        "canonical_url",
        "official_url",
        "attachment_urls",
        "content_hash",
        "document_content_hash",
        "table_location",
        "id",
        "report_id",
        "candidate_id",
        "discovery_run_id",
        "agent_run_id",
        "disallowed_outputs",
        "raw",
    }
)

#: Snapshot keys that are NOT statement datapoints.
_NON_FACT_SNAPSHOT_KEYS: frozenset[str] = frozenset(
    {
        "type",
        "source_tier",
        "data_provenance",
        "is_mock",
        "retrieved_at",
        "human_review_required",
        "note",
        "fundamentals_note",
        "current_period_note",
        # The four derived reporting STATES, not a datapoint. Checked by
        # ``_check_period_contradictions`` against the slots it summarises.
        "reporting_periods",
    }
)


@dataclass(frozen=True)
class ConsistencyFinding:
    """One detected contradiction, naming the sections that disagree."""

    invariant: str
    severity: str
    detail: str
    sections: tuple[str, ...] = ()

    @property
    def is_serious(self) -> bool:
        return self.severity == SEVERITY_SERIOUS


@dataclass
class ConsistencyAudit:
    """The full verdict for one report."""

    findings: list[ConsistencyFinding] = field(default_factory=list)
    checked_invariants: tuple[str, ...] = ALL_INVARIANTS

    @property
    def serious(self) -> list[ConsistencyFinding]:
        return [f for f in self.findings if f.is_serious]

    @property
    def is_clean(self) -> bool:
        """True when NO serious contradiction was found. Warnings are allowed."""
        return not self.serious

    def counts(self) -> dict[str, int]:
        return dict(Counter(f.invariant for f in self.findings))

    def summary(self) -> str:
        if not self.findings:
            return "No consistency findings."
        parts = [f"{name}={count}" for name, count in sorted(self.counts().items())]
        return f"{len(self.serious)} serious / {len(self.findings)} total — " + ", ".join(parts)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dp_value(section: dict[str, Any], key: str) -> Any:
    entry = section.get(key)
    if isinstance(entry, dict) and "value" in entry:
        return entry.get("value")
    return entry


def _snapshot_datapoints(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Statement datapoints that actually carry a value."""
    out: dict[str, dict[str, Any]] = {}
    for key, entry in snapshot.items():
        if key in _NON_FACT_SNAPSHOT_KEYS or not isinstance(entry, dict):
            continue
        if "value" not in entry and "numeric_value" not in entry:
            continue
        if entry.get("value") is None and entry.get("numeric_value") is None:
            continue
        out[key] = entry
    return out


def _canonical_field(key: str) -> str:
    """``revenue_primary_filing`` / ``revenue_usd_m`` -> ``revenue``."""
    for suffix in ("_primary_filing", "_current_period", "_usd_m", "_ttm_usd_m"):
        if key.endswith(suffix):
            return key[: -len(suffix)]
    return key


def _iter_text(node: Any, path: str = "") -> "list[tuple[str, str]]":
    """Every human-facing STRING in a section, with its key path.

    Machine-identifier keys are skipped: a URL containing the substring "None"
    is not a rendering defect, and flagging it would train a reader to ignore
    this check.
    """
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _NON_PROSE_KEYS:
                continue
            out.extend(_iter_text(value, f"{path}.{key}" if path else str(key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            out.extend(_iter_text(value, f"{path}[{index}]"))
    elif isinstance(node, str):
        out.append((path, node))
    return out


# --------------------------------------------------------------------------- #
# The audit
# --------------------------------------------------------------------------- #


def audit_report_consistency(
    report_content: dict[str, Any] | None,
    *,
    company_country: str | None = None,
    field_review_companies: "list[dict[str, Any]] | None" = None,
) -> ConsistencyAudit:
    """Run every invariant over one assembled report. Never raises."""
    audit = ConsistencyAudit()
    content = _as_dict(report_content)
    if not content:
        return audit

    for check in (
        _check_fact_present_and_missing,
        _check_primary_source_acquisition_gap,
        _check_scope_contradiction,
        _check_period_contradictions,
        _check_source_tier_contradiction,
        _check_channel_mismatch,
        _check_duplicate_documents,
        _check_duplicate_events,
        _check_text_leaks,
        _check_connector_state,
        _check_primary_filing_required,
        _check_jurisdiction_tasks,
        _check_fact_count_semantics,
    ):
        try:
            check(content, audit, company_country=company_country)
        except Exception as exc:  # noqa: BLE001 - an audit must never crash a run
            audit.findings.append(
                ConsistencyFinding(
                    invariant="audit_error",
                    severity=SEVERITY_WARNING,
                    detail=f"{check.__name__} raised {type(exc).__name__}",
                )
            )
    if field_review_companies:
        try:
            _check_dfr_field_gaps(field_review_companies, audit)
        except Exception as exc:  # noqa: BLE001
            audit.findings.append(
                ConsistencyFinding(
                    invariant="audit_error",
                    severity=SEVERITY_WARNING,
                    detail=f"dfr gap check raised {type(exc).__name__}",
                )
            )
    return audit


def _check_fact_present_and_missing(
    content: dict[str, Any], audit: ConsistencyAudit, **_: Any
) -> None:
    """A field cannot be BOTH shown with a value and listed as missing."""
    snapshot = _as_dict(content.get("financial_snapshot"))
    present = {_canonical_field(k) for k in _snapshot_datapoints(snapshot)}
    if not present:
        return

    missing_section = _as_dict(content.get("missing_information"))
    missing_items = _dp_value(missing_section, "missing_items") or []
    for item in missing_items:
        raw = item.get("field") if isinstance(item, dict) else item
        if not isinstance(raw, str):
            continue
        # ``financials.revenue`` / ``snapshot_financials.revenue`` -> ``revenue``
        leaf = raw.rsplit(".", 1)[-1].strip().lower()
        if leaf and leaf in present:
            audit.findings.append(
                ConsistencyFinding(
                    invariant=FACT_PRESENT_AND_MISSING,
                    severity=SEVERITY_SERIOUS,
                    detail=(
                        f"'{raw}' is listed as missing while the financial "
                        f"snapshot presents a value for '{leaf}'."
                    ),
                    sections=("financial_snapshot", "missing_information"),
                )
            )


def _check_primary_source_acquisition_gap(
    content: dict[str, Any], audit: ConsistencyAudit, **_: Any
) -> None:
    """"Source the annual report" must not appear once it IS ingested."""
    snapshot = _as_dict(content.get("financial_snapshot"))
    has_t1 = any(
        str(entry.get("source_tier", "")).startswith("T1")
        for entry in _snapshot_datapoints(snapshot).values()
    )
    if not has_t1:
        return

    review = _as_dict(content.get("source_quality_review"))
    for path, text in _iter_text(review):
        lowered = text.lower()
        acquisitional = any(
            phrase in lowered
            for phrase in (
                "t1_primary_filing required for financials",
                "primary filing required",
                "no primary filing",
            )
        )
        if acquisitional and "already ingested" not in lowered:
            audit.findings.append(
                ConsistencyFinding(
                    invariant=PRIMARY_SOURCE_PRESENT_BUT_ACQUISITION_GAP,
                    severity=SEVERITY_SERIOUS,
                    detail=(
                        "The report asks for a primary filing to be sourced while "
                        f"already presenting T1 datapoints ({path})."
                    ),
                    sections=("financial_snapshot", "source_quality_review"),
                )
            )


def _check_scope_contradiction(
    content: dict[str, Any], audit: ConsistencyAudit, **_: Any
) -> None:
    """A canonical Group slot must never hold a segment-scoped figure."""
    snapshot = _as_dict(content.get("financial_snapshot"))
    for key, entry in _snapshot_datapoints(snapshot).items():
        if not key.endswith(("_primary_filing", "_current_period")):
            continue
        scope = parse_scope(entry.get("scope"))
        if scope.is_segment:
            audit.findings.append(
                ConsistencyFinding(
                    invariant=SCOPE_CONTRADICTION,
                    severity=SEVERITY_SERIOUS,
                    detail=(
                        f"Canonical slot '{key}' holds a segment-scoped figure "
                        f"({scope.human_label()})."
                    ),
                    sections=("financial_snapshot",),
                )
            )

    # A historical series must not mix scopes inside one row.
    trends = _as_dict(content.get("historical_trends"))
    for row in _dp_value(trends, "series") or []:
        if not isinstance(row, dict):
            continue
        scopes = {
            (p.get("scope") if isinstance(p, dict) else None)
            for p in row.get("periods") or []
        }
        if len(scopes - {None}) > 1:
            audit.findings.append(
                ConsistencyFinding(
                    invariant=SCOPE_CONTRADICTION,
                    severity=SEVERITY_SERIOUS,
                    detail=(
                        f"Historical series '{row.get('metric')}' mixes scopes "
                        f"{sorted(s for s in scopes if s)}."
                    ),
                    sections=("historical_trends",),
                )
            )


def _check_period_contradictions(
    content: dict[str, Any], audit: ConsistencyAudit, **_: Any
) -> None:
    """Annual slots hold annual periods; interim slots hold interim periods."""
    snapshot = _as_dict(content.get("financial_snapshot"))
    datapoints = _snapshot_datapoints(snapshot)

    for key, entry in datapoints.items():
        period = parse_period(entry.get("period"))
        if key.endswith("_primary_filing") and period.is_interim:
            audit.findings.append(
                ConsistencyFinding(
                    invariant=INTERIM_AS_ANNUAL,
                    severity=SEVERITY_SERIOUS,
                    detail=(
                        f"Annual slot '{key}' holds an INTERIM period "
                        f"({period.label()})."
                    ),
                    sections=("financial_snapshot",),
                )
            )
        if key.endswith("_current_period") and period.period_type == PERIOD_TYPE_ANNUAL:
            audit.findings.append(
                ConsistencyFinding(
                    invariant=CURRENT_PERIOD_CONTRADICTION,
                    severity=SEVERITY_SERIOUS,
                    detail=(
                        f"Current-period slot '{key}' holds a full-year period "
                        f"({period.label()})."
                    ),
                    sections=("financial_snapshot",),
                )
            )

    # The four reporting states must agree with the slots they summarise.
    # Derived from those slots today, so a finding here means a future change
    # started computing them from something else.
    states = _as_dict(snapshot.get("reporting_periods"))
    if states:
        stated_annual = parse_period(states.get("latest_annual"))
        if stated_annual.is_interim:
            audit.findings.append(
                ConsistencyFinding(
                    invariant=INTERIM_AS_ANNUAL,
                    severity=SEVERITY_SERIOUS,
                    detail=(
                        "Reporting states name an INTERIM period as the latest "
                        f"ANNUAL period ({stated_annual.label()})."
                    ),
                    sections=("financial_snapshot",),
                )
            )
        stated_current = parse_period(states.get("latest_current_period"))
        if stated_current.period_type == PERIOD_TYPE_ANNUAL:
            audit.findings.append(
                ConsistencyFinding(
                    invariant=CURRENT_PERIOD_CONTRADICTION,
                    severity=SEVERITY_SERIOUS,
                    detail=(
                        "Reporting states name a FULL-YEAR period as the latest "
                        f"current period ({stated_current.label()})."
                    ),
                    sections=("financial_snapshot",),
                )
            )
        slot_annual = {
            parse_period(entry.get("period")).key
            for key, entry in datapoints.items()
            if key.endswith("_primary_filing")
        } - {None}
        if slot_annual and stated_annual.key and stated_annual.key not in slot_annual:
            audit.findings.append(
                ConsistencyFinding(
                    invariant=INTERIM_AS_ANNUAL,
                    severity=SEVERITY_SERIOUS,
                    detail=(
                        f"Reporting states name {stated_annual.label()} as the "
                        "latest annual period, which no annual slot holds "
                        f"({sorted(p for p in slot_annual if p)})."
                    ),
                    sections=("financial_snapshot",),
                )
            )

    # An annual slot must hold the LATEST annual period the report itself shows.
    trends = _as_dict(content.get("historical_trends"))
    latest_by_metric: dict[str, int] = {}
    for row in _dp_value(trends, "series") or []:
        if not isinstance(row, dict) or row.get("period_type") != PERIOD_TYPE_ANNUAL:
            continue
        if str(row.get("scope_type") or "") != "group":
            continue
        years: list[int] = [
            year
            for year in (
                parse_period(p.get("period")).year
                for p in row.get("periods") or []
                if isinstance(p, dict) and not p.get("superseded")
            )
            if year is not None
        ]
        if years:
            latest_by_metric[str(row.get("metric"))] = max(years)

    for key, entry in datapoints.items():
        if not key.endswith("_primary_filing"):
            continue
        metric = _canonical_field(key)
        newest = latest_by_metric.get(metric)
        shown = parse_period(entry.get("period"))
        if newest and shown.period_type == PERIOD_TYPE_ANNUAL and shown.year:
            # A slot that DISCLOSES the newer period is not presenting a
            # historical figure as current — it is stating exactly which period
            # it can stand behind and where the newer one is. That is the
            # honest resolution when the newer figure fell below the confidence
            # bar for a canonical slot; hiding either would be the defect.
            if entry.get("newer_period_available"):
                continue
            if shown.year < newest:
                audit.findings.append(
                    ConsistencyFinding(
                        invariant=HISTORICAL_AS_CURRENT,
                        severity=SEVERITY_SERIOUS,
                        detail=(
                            f"'{key}' shows FY{shown.year} while the report's own "
                            f"series carries a newer FY{newest} for '{metric}'."
                        ),
                        sections=("financial_snapshot", "historical_trends"),
                    )
                )


def _check_source_tier_contradiction(
    content: dict[str, Any], audit: ConsistencyAudit, **_: Any
) -> None:
    """A market-derived metric must never claim to be a filing fact."""
    snapshot = _as_dict(content.get("financial_snapshot"))
    market_keys = ("market_cap", "pe_ratio", "ev_", "enterprise_value")
    for key, entry in _snapshot_datapoints(snapshot).items():
        tier = str(entry.get("source_tier") or "")
        if any(key.startswith(m) for m in market_keys) and tier.startswith("T1"):
            audit.findings.append(
                ConsistencyFinding(
                    invariant=SOURCE_TIER_CONTRADICTION,
                    severity=SEVERITY_SERIOUS,
                    detail=(
                        f"Market-derived metric '{key}' claims a primary-filing "
                        f"tier ({tier})."
                    ),
                    sections=("financial_snapshot",),
                )
            )
        if key.endswith("_primary_filing") and tier and not tier.startswith("T1"):
            audit.findings.append(
                ConsistencyFinding(
                    invariant=SOURCE_TIER_CONTRADICTION,
                    severity=SEVERITY_SERIOUS,
                    detail=(
                        f"Primary-filing slot '{key}' carries a non-T1 tier ({tier})."
                    ),
                    sections=("financial_snapshot",),
                )
            )


def _check_channel_mismatch(
    content: dict[str, Any],
    audit: ConsistencyAudit,
    *,
    company_country: str | None = None,
    **_: Any,
) -> None:
    """An issuer-primary fact must not be described through a regulator channel.

    Also catches US filing vocabulary applied to a non-US issuer, which is the
    same defect seen from the other side.
    """
    snapshot = _as_dict(content.get("financial_snapshot"))
    note = _as_dict(snapshot.get("fundamentals_note"))
    source = str(note.get("fundamentals_source") or "")
    note_text = str(note.get("value") or "")
    if source.startswith("issuer_primary") and _US_FILING_RE.search(note_text):
        audit.findings.append(
            ConsistencyFinding(
                invariant=REGULATOR_VS_ISSUER_CHANNEL_MISMATCH,
                severity=SEVERITY_SERIOUS,
                detail=(
                    "Issuer-primary statement facts are described with US "
                    f"regulator vocabulary: {note_text[:160]}"
                ),
                sections=("financial_snapshot",),
            )
        )

    country = (company_country or "").strip()
    if not country or country in {"United States", "USA", "US"}:
        return
    for section_name in ("source_quality_review", "valuation_readiness"):
        section = _as_dict(content.get(section_name))
        for path, text in _iter_text(section):
            match = _US_FILING_RE.search(text)
            if match:
                audit.findings.append(
                    ConsistencyFinding(
                        invariant=REGULATOR_VS_ISSUER_CHANNEL_MISMATCH,
                        severity=SEVERITY_SERIOUS,
                        detail=(
                            f"US filing vocabulary '{match.group(0)}' used for a "
                            f"{country} issuer ({section_name}.{path})."
                        ),
                        sections=(section_name,),
                    )
                )


def _check_duplicate_documents(
    content: dict[str, Any], audit: ConsistencyAudit, **_: Any
) -> None:
    """The same document must not be counted twice."""
    section = _as_dict(content.get("primary_documents"))
    docs = _dp_value(section, "documents") or []
    hashes = [
        d.get("content_hash")
        for d in docs
        if isinstance(d, dict) and d.get("content_hash")
    ]
    for value, count in Counter(hashes).items():
        if count > 1:
            audit.findings.append(
                ConsistencyFinding(
                    invariant=DUPLICATE_DOCUMENT_IDENTITY,
                    severity=SEVERITY_WARNING,
                    detail=f"content_hash {str(value)[:12]}… appears {count} times.",
                    sections=("primary_documents",),
                )
            )


def _check_duplicate_events(
    content: dict[str, Any], audit: ConsistencyAudit, **_: Any
) -> None:
    """The same announcement must not appear once per channel."""
    from app.services.sources.disclosure_events import normalize_title

    section = _as_dict(content.get("news_catalyst_discovery"))
    items = _dp_value(section, "catalysts") or _dp_value(section, "items") or []
    keys: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("headline")
        date = item.get("date") or item.get("published_at") or ""
        if not title:
            continue
        keys.append((str(date)[:10], normalize_title(str(title))))
    for value, count in Counter(keys).items():
        if count > 1:
            audit.findings.append(
                ConsistencyFinding(
                    invariant=DUPLICATE_EVENT_IDENTITY,
                    severity=SEVERITY_WARNING,
                    detail=(
                        f"An announcement dated {value[0] or 'unknown'} appears "
                        f"{count} times."
                    ),
                    sections=("news_catalyst_discovery",),
                )
            )


# ── Manual-QA invariants ─────────────────────────────────────────────────── #
#
# Each of these was a real, live-observed contradiction in an otherwise
# "0 findings" report. They are text-layer checks by necessity — the defect IS
# the prose — but each is anchored to a STRUCTURED fact elsewhere in the same
# report, so a finding always means two parts of one document disagree, never
# that a sentence merely looked wrong.

#: Wording that asserts something is not live. Matched only against a report
#: that is simultaneously DISPLAYING live disclosures.
_CONNECTOR_NOT_LIVE_MARKERS: tuple[str, ...] = (
    "connector scaffolded",
    "not fetched at report time",
    "live retrieval is disabled",
    "scaffolded, not yet live",
    "pending regulator integration",
)

#: …AND the SUBJECT of that sentence must be the regulated-disclosure channel.
#:
#: Found by running the invariant against the regenerated reports: "Danish
#: -language business-press articles about … are not fetched at report time" is
#: TRUE, is about a T4 news reference rather than a filing venue, and was being
#: flagged as a contradiction. An invariant that fires on a true sentence is
#: itself a defect — it trains a reader to ignore the audit.
_REGULATED_DISCLOSURE_SUBJECT_MARKERS: tuple[str, ...] = (
    "regulated disclosure",
    "regulated-disclosure",
    "primary filing",
    "filing content",
    "regulator",
    "storage mechanism",
    "disclosure venue",
)

#: Generic demands for primary filings, forbidden once T1 filing facts exist.
_PRIMARY_FILING_REQUIRED_MARKERS: tuple[str, ...] = (
    "primary filings (t1/t2) required",
    "primary filings required",
    "no primary filing",
)

#: US/Canadian venues. Naming one as the place to verify a non-US issuer whose
#: own venue is known is the mismatch.
_US_CA_VENUE_MARKERS: tuple[str, ...] = ("sec edgar", "sedar+", "sedar")

_T1_TIERS: frozenset[str] = frozenset(
    {"T1_primary_filing", "T1_primary_company_source"}
)


def _live_disclosure_venues(content: dict[str, Any]) -> set[str]:
    """Venues this report actually DISPLAYS live disclosures from."""
    section = _as_dict(content.get("regulated_disclosures"))
    if not section.get("available"):
        return set()
    venues: set[str] = set()
    for event in _dp_value(section, "events") or []:
        if isinstance(event, dict) and isinstance(event.get("venue"), str):
            venues.add(event["venue"].strip())
    return {v for v in venues if v}


def _has_t1_primary_facts(content: dict[str, Any]) -> bool:
    """True when the report presents at least one T1 primary-filing datapoint."""
    snapshot = _as_dict(content.get("financial_snapshot"))
    for key, entry in _snapshot_datapoints(snapshot).items():
        if not key.endswith(("_primary_filing", "_current_period")):
            continue
        if str(entry.get("source_tier") or "") in _T1_TIERS:
            return True
    return False


def _check_connector_state(
    content: dict[str, Any], audit: ConsistencyAudit, **_: Any
) -> None:
    """Live disclosures displayed AND the venue called not-live."""
    venues = _live_disclosure_venues(content)
    if not venues:
        return
    for path, text in _iter_text(content):
        if path.startswith("regulated_disclosures"):
            continue
        lowered = text.lower()
        marker = next(
            (m for m in _CONNECTOR_NOT_LIVE_MARKERS if m in lowered), None
        )
        if marker is None:
            continue
        if not any(m in lowered for m in _REGULATED_DISCLOSURE_SUBJECT_MARKERS):
            # A true "not fetched" statement about a DIFFERENT channel (the
            # local-language business press, an aggregator) is not a
            # contradiction with a live filing venue.
            continue
        audit.findings.append(
            ConsistencyFinding(
                invariant=CONNECTOR_STATE_CONTRADICTION,
                severity=SEVERITY_SERIOUS,
                detail=(
                    f"The report displays live regulated disclosures from "
                    f"{sorted(venues)} while stating '{marker}' at {path}."
                ),
                sections=("regulated_disclosures",),
            )
        )
        return


def _check_primary_filing_required(
    content: dict[str, Any], audit: ConsistencyAudit, **_: Any
) -> None:
    """T1 filing facts present AND a generic demand for primary filings."""
    if not _has_t1_primary_facts(content):
        return
    for path, text in _iter_text(content):
        lowered = text.lower()
        marker = next(
            (m for m in _PRIMARY_FILING_REQUIRED_MARKERS if m in lowered), None
        )
        if marker is None:
            continue
        audit.findings.append(
            ConsistencyFinding(
                invariant=PRIMARY_FILING_REQUIRED_CONTRADICTION,
                severity=SEVERITY_SERIOUS,
                detail=(
                    "The report presents validated T1 primary-filing datapoints "
                    f"while stating '{marker}' at {path}, as though none existed."
                ),
                sections=("financial_snapshot",),
            )
        )
        return


def _check_jurisdiction_tasks(
    content: dict[str, Any], audit: ConsistencyAudit, **_: Any
) -> None:
    """A non-US issuer told to verify itself against SEC EDGAR / SEDAR+.

    Gated on the report's OWN identity: an issuer whose exchange or domicile
    this report does not state is not judged, and a US issuer is never flagged —
    SEC EDGAR is genuinely where it should be verified.
    """
    identity = _as_dict(content.get("company_identity"))
    country = str(_dp_value(identity, "country_domicile") or "").strip().lower()
    if not country or country in {"united states", "usa", "us"}:
        return
    for path, text in _iter_text(content):
        lowered = text.lower()
        if not any(m in lowered for m in _US_CA_VENUE_MARKERS):
            continue
        # Only a TASK/next-step recommendation is a mismatch. A gap explaining
        # that SEC EDGAR does not cover this issuer is correct and necessary.
        if not any(
            verb in lowered
            for verb in ("cross-check", "cross check", "verify against", "obtain from")
        ):
            continue
        audit.findings.append(
            ConsistencyFinding(
                invariant=JURISDICTION_TASK_MISMATCH,
                severity=SEVERITY_SERIOUS,
                detail=(
                    f"A {country.title()} issuer is directed to a US/Canadian "
                    f"venue at {path}: {text[:120]}"
                ),
                sections=("research_completeness_review",),
            )
        )
        return


def _check_fact_count_semantics(
    content: dict[str, Any], audit: ConsistencyAudit, **_: Any
) -> None:
    """Two displayed fact counts must agree, or say they count different things.

    A document row reading "0 fact(s)" beside a report-level "primary fact
    count: 8" drawn from that same document is unreadable unless the row states
    what it counts. Either is acceptable; silence is not.
    """
    memo = _as_dict(content.get("research_memo"))
    summary = _as_dict(memo.get("primary_evidence_summary"))
    if not summary:
        return
    report_total = summary.get("primary_fact_count")
    if not isinstance(report_total, int):
        return
    rows = summary.get("primary_documents")
    if not isinstance(rows, list):
        return
    row_total = sum(
        int(r.get("fact_count") or 0) for r in rows if isinstance(r, dict)
    )
    if row_total == report_total:
        return
    unlabelled = [
        r
        for r in rows
        if isinstance(r, dict) and not str(r.get("counts_basis") or "").strip()
    ]
    if not unlabelled:
        return
    audit.findings.append(
        ConsistencyFinding(
            invariant=FACT_COUNT_SEMANTICS_MISMATCH,
            severity=SEVERITY_SERIOUS,
            detail=(
                f"Per-document fact counts total {row_total} while the report "
                f"states {report_total}, and "
                f"{len(unlabelled)} document row(s) do not say which population "
                "they count."
            ),
            sections=("research_memo",),
        )
    )


def _check_text_leaks(
    content: dict[str, Any], audit: ConsistencyAudit, **_: Any
) -> None:
    """The two classes that genuinely ARE about rendered text."""
    for path, text in _iter_text(content):
        if _NONE_LITERAL_RE.search(text):
            audit.findings.append(
                ConsistencyFinding(
                    invariant=NONE_LITERAL_LEAK,
                    severity=SEVERITY_SERIOUS,
                    detail=f"Python literal 'None' rendered at {path}: {text[:120]}",
                    sections=(path.split(".", 1)[0],),
                )
            )
        match = _ENUM_REPR_RE.search(text)
        if match:
            audit.findings.append(
                ConsistencyFinding(
                    invariant=ENUM_REPR_LEAK,
                    severity=SEVERITY_SERIOUS,
                    detail=f"Enum repr '{match.group(0)}' rendered at {path}.",
                    sections=(path.split(".", 1)[0],),
                )
            )


def _check_dfr_field_gaps(
    companies: "list[dict[str, Any]]", audit: ConsistencyAudit
) -> None:
    """One company's missing field must never be attributed to another.

    Operates on the DFR pack's own per-company completeness lists, which is what
    makes this checkable at all: before those existed the claim lived only in
    free-text council prose and could not be verified deterministically.
    """
    for company in companies:
        if not isinstance(company, dict):
            continue
        present = {str(f) for f in company.get("identity_fields_present") or []}
        missing = {str(f) for f in company.get("identity_fields_missing") or []}
        overlap = present & missing
        if overlap:
            audit.findings.append(
                ConsistencyFinding(
                    invariant=DFR_FIELD_GAP_FALSE_POSITIVE,
                    severity=SEVERITY_SERIOUS,
                    detail=(
                        f"{company.get('id') or 'company'} lists "
                        f"{sorted(overlap)} as BOTH present and missing."
                    ),
                    sections=("field_review",),
                )
            )


__all__ = [
    "ALL_INVARIANTS",
    "CURRENT_PERIOD_CONTRADICTION",
    "DFR_FIELD_GAP_FALSE_POSITIVE",
    "DUPLICATE_DOCUMENT_IDENTITY",
    "DUPLICATE_EVENT_IDENTITY",
    "ENUM_REPR_LEAK",
    "FACT_PRESENT_AND_MISSING",
    "HISTORICAL_AS_CURRENT",
    "INTERIM_AS_ANNUAL",
    "NONE_LITERAL_LEAK",
    "PRIMARY_SOURCE_PRESENT_BUT_ACQUISITION_GAP",
    "REGULATOR_VS_ISSUER_CHANNEL_MISMATCH",
    "SCOPE_CONTRADICTION",
    "SERIOUS_INVARIANTS",
    "SEVERITY_SERIOUS",
    "SEVERITY_WARNING",
    "SOURCE_TIER_CONTRADICTION",
    "ConsistencyAudit",
    "ConsistencyFinding",
    "audit_report_consistency",
]
