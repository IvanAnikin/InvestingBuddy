"""ONE name, and one definition, for each population a "fact count" can mean.

Manual-QA reconciliation. A single report was displaying six different numbers
under wording a reader could only read as the same thing:

    research memo, per document          "1 fact(s)" / "9 fact(s)" / "4 fact(s)"
    research memo, total                 "Primary Fact Count = 34"
    Primary Documents tab, per document  9 / 24 / 1
    Primary Documents tab, summary       "Validated facts: 34"

Every one of those numbers was correct. They count DIFFERENT populations — the
evidence-pack items a council may cite, the complete high-confidence set the
report presents, the active rows persisted for one document — and nothing on
the page said so, so the only available reading was that the report contradicted
itself. Richemont's "4" and "24" are the same document.

The rule this module exists to enforce is not "make the numbers match". Forcing
agreement would mean either hiding facts the report holds or inflating a count
past the rows that exist, and both are worse than the confusion. The rule is:

    every displayed fact count names its own scope, and two counts that share a
    scope must agree.

So this is a closed vocabulary, not a formatting helper. Adding a new count
means adding a scope here — which is what makes the invariant in
``report_consistency`` able to check the rule at all.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FactCountScope:
    """One population, its short human name, and what it actually counts."""

    key: str
    label: str
    definition: str


#: Active ``ExtractedFact`` rows persisted for ONE document. The deepest,
#: most complete number: everything the extractor validated out of that
#: document and the database still holds as current. Superseded rows are
#: excluded — they are kept for audit, never counted twice.
PERSISTED_VALIDATED = FactCountScope(
    key="persisted_validated_facts",
    label="Persisted validated facts",
    definition=(
        "Facts extracted from this document and still held as current in the "
        "database. Superseded revalidations are excluded."
    ),
)

#: The high-confidence primary facts the REPORT presents, across every
#: ingested document. Narrower than the persisted set (it applies the
#: canonical-slot confidence bar) and wider than any single document.
REPORT_PRIMARY = FactCountScope(
    key="report_primary_facts",
    label="Report primary facts",
    definition=(
        "The high-confidence primary facts this report presents, across every "
        "ingested document."
    ),
)

#: Fact-shaped evidence items built for the council from one document. Bounded
#: by the evidence budget, so it is normally SMALLER than that document's
#: persisted set — a low number here means "not everything reached the council",
#: never "this document produced nothing".
CITED_EVIDENCE = FactCountScope(
    key="cited_evidence_facts",
    label="Cited evidence facts",
    definition=(
        "Fact-shaped evidence items built from this document for the council to "
        "cite. Bounded by the evidence budget, so it is normally fewer than the "
        "document's persisted validated facts."
    ),
)

#: DISTINCT canonical statement FIELDS resolved (revenue, EBIT, …) — not a
#: fact count at all. Named here so it can never be labelled as one: five
#: fields can be resolved from thirty-four facts without contradiction.
CANONICAL_FIELDS = FactCountScope(
    key="canonical_statement_fields",
    label="Canonical statement fields",
    definition=(
        "Distinct financial-statement fields resolved (revenue, EBIT, …). A "
        "field count, not a fact count — many facts can resolve one field."
    ),
)

ALL_SCOPES: tuple[FactCountScope, ...] = (
    PERSISTED_VALIDATED,
    REPORT_PRIMARY,
    CITED_EVIDENCE,
    CANONICAL_FIELDS,
)

_BY_KEY: dict[str, FactCountScope] = {s.key: s for s in ALL_SCOPES}


def scope_for(key: str | None) -> FactCountScope | None:
    """The scope with this key, or ``None`` for an unknown one."""
    return _BY_KEY.get((key or "").strip())


def label_for(key: str | None) -> str | None:
    scope = scope_for(key)
    return scope.label if scope else None


def definitions() -> dict[str, str]:
    """Every scope's definition, for a report to state once rather than per row."""
    return {s.key: s.definition for s in ALL_SCOPES}


__all__ = [
    "ALL_SCOPES",
    "CANONICAL_FIELDS",
    "CITED_EVIDENCE",
    "PERSISTED_VALIDATED",
    "REPORT_PRIMARY",
    "FactCountScope",
    "definitions",
    "label_for",
    "scope_for",
]
