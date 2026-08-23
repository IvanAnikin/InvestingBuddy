"""
Canonical human-facing research-state presentation — Phase C.

WHY THIS MODULE EXISTS
======================
Manual QA found ONE report describing its own evidence as ``strong``,
``adequate`` and ``weak`` in different sections, because each section computed
its own answer from whatever it happened to hold. Phase B gave the platform one
typed EVIDENCE state; this module gives it one typed PRESENTATION of that
state, so every human surface reads the same judgement instead of recomputing
a private one.

Two contracts live here:

* :class:`SourceQualityAssessment` — evidence quality across four SEPARATE
  dimensions. A single overloaded label was the original problem: "quality" of
  what? Identity, financial facts and catalysts genuinely differ, and
  collapsing them forces a section to pick a number it cannot justify.

* :class:`WarningGroup` / :class:`WarningCollector` — canonical, deduplicated,
  severity-classified warnings. A real European discovery run produced ~200
  warning strings, mostly the same handful repeated per candidate. Grouping is
  presentation only: raw instances are retained for diagnostics, and a BLOCKING
  warning is never merged away.

Neither contract changes evidence semantics. They change what a human is shown
about evidence that Phase B already established.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Bump when the persisted presentation payload changes shape.
RESEARCH_QUALITY_SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Source quality
# ---------------------------------------------------------------------------

QUALITY_STRONG = "strong"
QUALITY_ADEQUATE = "adequate"
QUALITY_WEAK = "weak"
QUALITY_INSUFFICIENT = "insufficient"

# Worst-first, so an overall label can be derived by taking the weakest
# contributing dimension without inventing a scoring scheme.
_QUALITY_ORDER = (
    QUALITY_INSUFFICIENT,
    QUALITY_WEAK,
    QUALITY_ADEQUATE,
    QUALITY_STRONG,
)


def _weakest(labels: list[str]) -> str:
    """The weakest of several labels — never an average.

    Averaging would let strong identity data mask absent financials, which is
    precisely the kind of flattering summary this platform must not produce.
    """
    present = [lbl for lbl in labels if lbl in _QUALITY_ORDER]
    if not present:
        return QUALITY_INSUFFICIENT
    return min(present, key=_QUALITY_ORDER.index)


class QualityDimension(BaseModel):
    """One evidence dimension: a label plus the reasons that produced it.

    ``basis`` exists so a human never has to trust a bare adjective. Every
    entry is machine-generated from the evidence inventory, so the label and
    its justification cannot drift apart.
    """

    model_config = ConfigDict(extra="forbid")

    label: str = QUALITY_INSUFFICIENT
    basis: list[str] = Field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {"label": self.label, "basis": list(self.basis)}


class SourceQualityAssessment(BaseModel):
    """The ONE source-quality answer every human surface renders.

    Computed once from the final reconciled evidence inventory by
    :func:`assess_source_quality`. Sections read this; they do not recompute.
    """

    model_config = ConfigDict(extra="forbid")

    identity_quality: QualityDimension = Field(default_factory=QualityDimension)
    financial_evidence_quality: QualityDimension = Field(
        default_factory=QualityDimension
    )
    catalyst_evidence_quality: QualityDimension = Field(
        default_factory=QualityDimension
    )
    overall_research_evidence_quality: QualityDimension = Field(
        default_factory=QualityDimension
    )
    schema_version: int = RESEARCH_QUALITY_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "identity_quality": self.identity_quality.to_payload(),
            "financial_evidence_quality": self.financial_evidence_quality.to_payload(),
            "catalyst_evidence_quality": self.catalyst_evidence_quality.to_payload(),
            "overall_research_evidence_quality": (
                self.overall_research_evidence_quality.to_payload()
            ),
        }


def assess_source_quality(
    *,
    inventory: Any = None,
    identity: dict[str, Any] | None = None,
    catalyst_summary: dict[str, Any] | None = None,
    primary_fact_count: int = 0,
) -> SourceQualityAssessment:
    """Derive all four dimensions from final reconciled state, in one place.

    ``inventory`` is an ``EvidenceInventory`` (Phase B). Everything here is a
    presentation judgement over evidence that already exists — no fetching, no
    recomputation of facts, and nothing invented when a dimension is unknown.
    """
    identity = identity or {}
    catalyst_summary = catalyst_summary or {}

    # -- identity ----------------------------------------------------------
    id_basis: list[str] = []
    strong_ids = [k for k in ("isin", "lei", "cik") if identity.get(k)]
    core_ids = [k for k in ("legal_name", "ticker", "exchange") if identity.get(k)]
    if strong_ids:
        id_basis.append(f"strong identifier(s) present: {', '.join(strong_ids)}")
    if core_ids:
        id_basis.append(f"core identity fields present: {', '.join(core_ids)}")
    if len(core_ids) < 3:
        id_basis.append("one or more core identity fields not sourced")
    if strong_ids and len(core_ids) == 3:
        identity_label = QUALITY_STRONG
    elif len(core_ids) == 3:
        identity_label = QUALITY_ADEQUATE
        id_basis.append("no ISIN/LEI/CIK identifier sourced")
    elif core_ids:
        identity_label = QUALITY_WEAK
    else:
        identity_label = QUALITY_INSUFFICIENT
        id_basis.append("company identity not sourced")

    # -- financial evidence ------------------------------------------------
    fin_basis: list[str] = []
    fundamentals = getattr(inventory, "fundamentals", None)
    fin_label = QUALITY_INSUFFICIENT
    if fundamentals is not None and getattr(fundamentals, "available", False):
        if getattr(fundamentals, "regulator_facts_available", False):
            fin_label = QUALITY_STRONG
            fin_basis.append("regulator-structured financial facts available")
        elif getattr(fundamentals, "issuer_primary_facts_available", False):
            fin_label = QUALITY_STRONG
            fin_basis.append("issuer primary-document financial facts available")
        else:
            fin_label = QUALITY_ADEQUATE
            fin_basis.append("aggregator fundamentals available; not filing-verified")
        period = getattr(fundamentals, "period_label", None)
        if period:
            fin_basis.append(f"period: {period}")
        count = getattr(fundamentals, "fact_count", 0)
        if count:
            fin_basis.append(f"{count} statement value(s) sourced")
    elif int(primary_fact_count or 0) > 0:
        # Facts extracted from an issuer primary document are real financial
        # evidence. Requiring resolved "fundamentals" as well would repeat the
        # source-METHOD-vs-fact-ABSENCE conflation this platform keeps fixing.
        fin_label = QUALITY_STRONG
        fin_basis.append(
            f"{int(primary_fact_count)} validated primary-document financial "
            "fact(s) extracted"
        )
    else:
        financial_data = getattr(inventory, "financial_data", None)
        statement_fields = [
            f
            for f in (getattr(financial_data, "available_fields", []) or [])
            if str(f).startswith("financials.")
        ]
        if statement_fields:
            fin_label = QUALITY_WEAK
            fin_basis.append(
                f"{len(statement_fields)} financial field(s) listed without "
                "resolved statement fundamentals"
            )
        else:
            fin_basis.append("no financial statement facts sourced")

    # -- catalyst evidence -------------------------------------------------
    cat_basis: list[str] = []
    filings = int(catalyst_summary.get("regulator_filing_count") or 0)
    press = int(catalyst_summary.get("issuer_press_count") or 0)
    independent = int(catalyst_summary.get("independent_news_count") or 0)
    if filings:
        cat_basis.append(f"{filings} regulator filing event(s)")
    if press:
        cat_basis.append(f"{press} issuer press item(s)")
    if independent:
        cat_basis.append(f"{independent} independent news item(s)")
    if filings and (press or independent):
        cat_label = QUALITY_STRONG
    elif filings or press:
        cat_label = QUALITY_ADEQUATE
        if not independent:
            cat_basis.append("limited independent coverage")
    elif independent:
        cat_label = QUALITY_WEAK
    else:
        cat_label = QUALITY_INSUFFICIENT
        cat_basis.append("no catalyst evidence sourced")

    # -- overall -----------------------------------------------------------
    # The WEAKEST contributing dimension, never an average: research is only as
    # good as the evidence it is missing.
    overall_label = _weakest([identity_label, fin_label, cat_label])
    overall_basis = [
        f"identity: {identity_label}",
        f"financial evidence: {fin_label}",
        f"catalyst evidence: {cat_label}",
        "overall reflects the weakest contributing dimension",
    ]

    return SourceQualityAssessment(
        identity_quality=QualityDimension(label=identity_label, basis=id_basis),
        financial_evidence_quality=QualityDimension(label=fin_label, basis=fin_basis),
        catalyst_evidence_quality=QualityDimension(label=cat_label, basis=cat_basis),
        overall_research_evidence_quality=QualityDimension(
            label=overall_label, basis=overall_basis
        ),
    )


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_BLOCKING = "blocking"

SCOPE_RUN = "run"
SCOPE_CANDIDATE = "candidate"

# Canonical codes. A code is a SEMANTIC identity: two raw strings that mean the
# same thing collapse to one group, and two that differ never do.
CODE_PRICE_FALLBACK_USED = "PRICE_FALLBACK_USED"
CODE_NO_PRIMARY_IR_CONTENT = "NO_PRIMARY_IR_CONTENT"
CODE_NO_REGULATOR_CONNECTOR = "NO_REGULATOR_CONNECTOR"
CODE_NO_FUNDAMENTALS = "NO_FUNDAMENTALS"
CODE_NEWS_PROVIDER_NO_RESULTS = "NEWS_PROVIDER_NO_RESULTS"
CODE_IDENTITY_INCOMPLETE = "IDENTITY_INCOMPLETE"
CODE_COUNCIL_PARTIAL = "COUNCIL_PARTIAL"
CODE_SOURCE_QUALITY_WEAK = "SOURCE_QUALITY_WEAK"
CODE_CITATION_TIER_LOW = "CITATION_TIER_LOW"
CODE_UNCLASSIFIED = "UNCLASSIFIED"

# Ordered most-specific-first: the first match wins, so a broad pattern can
# never capture a message a narrower rule explains better.
_CLASSIFIERS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        CODE_PRICE_FALLBACK_USED,
        SEVERITY_INFO,
        "Price sourced from a fallback provider.",
        ("price provider unavailable", "price-only fallback", "used eodhd price"),
    ),
    (
        CODE_NEWS_PROVIDER_NO_RESULTS,
        SEVERITY_INFO,
        "A configured news provider returned no results in the lookback window.",
        ("returned no company results", "news provider", "no news results"),
    ),
    (
        CODE_NO_FUNDAMENTALS,
        SEVERITY_WARNING,
        "Financial fundamentals were not sourced.",
        (
            "financial fundamental categories missing",
            "fundamentals not sourced",
            "fundamentals_not_sourced",
            "no fundamentals",
        ),
    ),
    (
        CODE_NO_PRIMARY_IR_CONTENT,
        SEVERITY_WARNING,
        "No issuer primary-document content was extracted.",
        (
            "annual report",
            "issuer ir",
            "primary document",
            "no primary content",
        ),
    ),
    (
        CODE_NO_REGULATOR_CONNECTOR,
        SEVERITY_INFO,
        "No regulator connector applies to this listing.",
        ("no regulator", "regulator connector", "no sec mapping", "cik"),
    ),
    (
        CODE_IDENTITY_INCOMPLETE,
        SEVERITY_INFO,
        "Some company identity fields were not sourced.",
        ("identity.", "isin", "lei not"),
    ),
    (
        CODE_CITATION_TIER_LOW,
        SEVERITY_WARNING,
        "Some citations rest on aggregator-tier sources only.",
        ("t5_api_aggregator only", "citation from", "upgrade to t1/t2"),
    ),
    (
        CODE_COUNCIL_PARTIAL,
        SEVERITY_WARNING,
        "The LLM council did not complete every agent.",
        ("budget_exhausted", "llmratelimiterror", "llmjsonerror", "council"),
    ),
    (
        CODE_SOURCE_QUALITY_WEAK,
        SEVERITY_WARNING,
        "Overall source quality is weak.",
        ("source quality", "weak_or_stale"),
    ),
)


def classify_warning(message: str) -> tuple[str, str, str]:
    """Map a raw warning string to ``(code, severity, user_message)``.

    Unrecognised messages become ``UNCLASSIFIED`` and are still shown — an
    unknown warning is never dropped just because no rule matched it.
    """
    text = (message or "").lower()
    for code, severity, user_message, needles in _CLASSIFIERS:
        if any(needle in text for needle in needles):
            return code, severity, user_message
    return CODE_UNCLASSIFIED, SEVERITY_WARNING, (message or "").strip()


class WarningGroup(BaseModel):
    """One canonical warning, with how many raw instances it represents."""

    model_config = ConfigDict(extra="forbid")

    code: str
    severity: str = SEVERITY_WARNING
    scope: str = SCOPE_RUN
    message: str = ""
    count: int = 0
    # Entities (tickers / candidate ids) the warning applies to, bounded.
    subjects: list[str] = Field(default_factory=list)
    # A few raw instances so a human can see the original wording.
    samples: list[str] = Field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "scope": self.scope,
            "message": self.message,
            "count": self.count,
            "subjects": list(self.subjects),
            "samples": list(self.samples),
        }


class WarningCollector:
    """Collects raw warnings and presents a small, bounded, honest summary.

    Grouping is PRESENTATION ONLY. Raw instances are retained
    (``raw_instances``) for diagnostics, and BLOCKING warnings are always
    surfaced individually — deduplication must never make a blocker quieter.
    """

    #: Hard bound on groups shown to a human.
    MAX_GROUPS = 8
    #: Raw samples retained per group.
    MAX_SAMPLES = 3
    #: Subjects listed per group before truncation.
    MAX_SUBJECTS = 10

    def __init__(self) -> None:
        self.raw_instances: list[dict[str, Any]] = []

    def add(
        self,
        message: str,
        *,
        subject: str | None = None,
        scope: str = SCOPE_CANDIDATE,
        severity: str | None = None,
    ) -> None:
        code, inferred_severity, user_message = classify_warning(message)
        self.raw_instances.append(
            {
                "code": code,
                "severity": severity or inferred_severity,
                "scope": scope,
                "message": user_message,
                "subject": subject,
                "raw": message,
            }
        )

    def add_many(
        self, messages: list[str], *, subject: str | None = None, scope: str = SCOPE_CANDIDATE
    ) -> None:
        for message in messages or []:
            self.add(message, subject=subject, scope=scope)

    def groups(self) -> list[WarningGroup]:
        """Deduplicated groups, most severe and most frequent first."""
        grouped: OrderedDict[tuple[str, str], WarningGroup] = OrderedDict()
        blocking: list[WarningGroup] = []

        for inst in self.raw_instances:
            if inst["severity"] == SEVERITY_BLOCKING:
                # Never merged: a blocker must stay individually visible.
                blocking.append(
                    WarningGroup(
                        code=inst["code"],
                        severity=SEVERITY_BLOCKING,
                        scope=inst["scope"],
                        message=inst["message"],
                        count=1,
                        subjects=[inst["subject"]] if inst["subject"] else [],
                        samples=[inst["raw"]],
                    )
                )
                continue
            # UNCLASSIFIED keys on its own text so genuinely different unknown
            # problems are not silently merged into one bucket.
            key_text = inst["message"] if inst["code"] == CODE_UNCLASSIFIED else ""
            key = (inst["code"], key_text)
            group = grouped.get(key)
            if group is None:
                group = WarningGroup(
                    code=inst["code"],
                    severity=inst["severity"],
                    scope=inst["scope"],
                    message=inst["message"],
                )
                grouped[key] = group
            group.count += 1
            if inst["subject"] and inst["subject"] not in group.subjects:
                if len(group.subjects) < self.MAX_SUBJECTS:
                    group.subjects.append(inst["subject"])
            if len(group.samples) < self.MAX_SAMPLES:
                group.samples.append(inst["raw"])

        for group in grouped.values():
            # A warning affecting several subjects is a RUN-level observation.
            if len(group.subjects) > 1:
                group.scope = SCOPE_RUN

        severity_rank = {SEVERITY_BLOCKING: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}
        ordered = sorted(
            grouped.values(),
            key=lambda g: (severity_rank.get(g.severity, 3), -g.count, g.code),
        )
        return blocking + ordered[: self.MAX_GROUPS]

    def to_payload(self) -> dict[str, Any]:
        """Versioned payload: grouped for humans, raw retained for diagnostics."""
        return {
            "warnings_schema_version": RESEARCH_QUALITY_SCHEMA_VERSION,
            "groups": [g.to_payload() for g in self.groups()],
            "raw_instance_count": len(self.raw_instances),
        }

    @classmethod
    def from_messages(
        cls, messages: list[str], *, scope: str = SCOPE_RUN
    ) -> "WarningCollector":
        """Build from legacy ``"TICKER: message"`` strings.

        The historical discovery blob is a flat list of prefixed strings; the
        prefix is the subject, which is what makes per-candidate repetition
        groupable at run level.
        """
        collector = cls()
        for raw in messages or []:
            subject = None
            message = raw
            if ":" in raw:
                head, tail = raw.split(":", 1)
                # A short, token-like prefix is a ticker, not prose.
                if head and len(head) <= 12 and " " not in head.strip():
                    subject, message = head.strip(), tail.strip()
            collector.add(message, subject=subject, scope=scope)
        return collector


# ---------------------------------------------------------------------------
# Thin-evidence research state
# ---------------------------------------------------------------------------


class ThinEvidenceAssessment(BaseModel):
    """Whether a company has too little evidence for a full research report.

    Live motivation: a metadata-only issuer correctly failed closed, but still
    rendered the entire report skeleton — twenty sections of "Not sourced" plus
    Bull/Bear/Risk blocks reasoning about evidence that does not exist. Failing
    closed is right; looking broken while doing it is not.

    The trigger is deterministic and evidence-based. It names no company and
    reads only the final reconciled inventory.
    """

    model_config = ConfigDict(extra="forbid")

    is_thin: bool = False
    reasons: list[str] = Field(default_factory=list)
    # What the company DOES have, so the short form can lead with it.
    has_price: bool = False
    has_identity: bool = False
    known_source_locations: list[str] = Field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "is_thin": self.is_thin,
            "reasons": list(self.reasons),
            "has_price": self.has_price,
            "has_identity": self.has_identity,
            "known_source_locations": list(self.known_source_locations),
        }


def assess_thin_evidence(
    *,
    inventory: Any = None,
    identity: dict[str, Any] | None = None,
    primary_fact_count: int = 0,
    catalyst_summary: dict[str, Any] | None = None,
    source_locations: list[str] | None = None,
) -> ThinEvidenceAssessment:
    """Decide whether to render the SHORT-FORM research state.

    Thin means all three of:
      * no resolved financial-statement fundamentals, AND
      * no extracted primary-document financial facts, AND
      * no catalyst evidence.

    A company with ANY of those gets the normal full report — the short form is
    for genuine absence, not for merely incomplete evidence.
    """
    identity = identity or {}
    catalyst_summary = catalyst_summary or {}

    fundamentals = getattr(inventory, "fundamentals", None)
    has_fundamentals = bool(fundamentals is not None and getattr(fundamentals, "available", False))
    has_primary_facts = int(primary_fact_count or 0) > 0
    catalyst_total = sum(
        int(catalyst_summary.get(key) or 0)
        for key in ("regulator_filing_count", "issuer_press_count", "independent_news_count")
    )

    reasons: list[str] = []
    if not has_fundamentals:
        reasons.append("no financial-statement fundamentals resolved")
    if not has_primary_facts:
        reasons.append("no primary-document financial facts extracted")
    if catalyst_total == 0:
        reasons.append("no catalyst evidence sourced")

    is_thin = not has_fundamentals and not has_primary_facts and catalyst_total == 0

    price = getattr(inventory, "price", None)
    return ThinEvidenceAssessment(
        is_thin=is_thin,
        reasons=reasons if is_thin else [],
        has_price=bool(price is not None and getattr(price, "available", False)),
        has_identity=bool(identity.get("legal_name") or identity.get("ticker")),
        known_source_locations=[str(x) for x in (source_locations or [])][:10],
    )
