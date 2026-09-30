"""Final report reconciliation — gap closure and temporal supersession.

THE DEFECT
==========
Five producers state what a run does not know, and none of them reads the findings: the
ledger gap a specialist wrote under one question, the professional report's platform
evidence gaps, the Chair's GAPS block, and the V2 ``missing_information`` list and
council concerns (assembled before V3 ran at all). So a report could print "no capital
expenditure was acquired" beside a finding that states the capex — written under a
different question, or in a later round, or from a document acquired after the V2 report
was assembled. ``ledger.close_gap`` existed and had no caller.

And two findings about the same milestone of the same project — "first production in
2028" from last year's announcement and "first production in 2027" from this quarter's —
were shown side by side as if both were current.

WHAT THIS DOES
==============
Runs once, after the Red Team and before the Chair and the report are assembled:

1. **Supersession.** Findings stating the same field (``research_fields``) of the same
   project in the same scope, with different values, are ordered by the publication date
   of their evidence. The newest is current guidance; each older one is kept and marked
   superseded by it. Never across scope or project; never between two real financial
   periods (FY2024 and FY2025 are both true); unknown or equal dates → a recorded
   DISAGREEMENT, never a silent pick.
2. **Gap reconciliation.** Each gap becomes exactly one of

   * ``closed`` — a non-withdrawn finding affirmatively states every field the gap names,
     in a compatible scope and project, for the period asked, from the issuer's own or
     official evidence. Persisted through ``ledger.close_gap``, naming the finding;
   * ``partially_closed`` — a finding states it, but for an older period, another scope,
     an unnamed project, from third-party evidence only, or the gap's field was only
     inferred from its question. Shown, with the finding;
   * ``superseded`` — the acquisition failure the gap records was overcome: the document
     it names is now held, or a validated fact states the field;
   * ``still_open`` — everything else, including every gap whose field is unknown.

FAIL CLOSED
===========
A gap whose text names no field is never closed. A negated clause never closes anything.
Scope, project and period must be compatible. Nothing here names an issuer.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.services import research_fields as rf
from app.services.ledger import store as ledger

#: Source kinds that speak for the issuer or an official authority. A finding resting on
#: none of these is third-party only and closes nothing outright.
PRIMARY_SOURCE_KINDS: frozenset[str] = frozenset(
    {"issuer_filing", "issuer_ir", "government_or_regulator", "platform_calculation"}
)
#: Source kinds that are the issuer's own documents — what "N from issuer documents"
#: counts.
ISSUER_SOURCE_KINDS: frozenset[str] = frozenset({"issuer_filing", "issuer_ir"})

#: Gap types that record a failure to ACQUIRE — the ones a later acquisition supersedes.
ACQUISITION_GAP_TYPES: frozenset[str] = frozenset(
    {ledger.GAP_SOURCE_UNREACHABLE, ledger.GAP_TOOL_UNAVAILABLE, ledger.GAP_EVIDENCE_UNAVAILABLE}
)

_READY_STATES = frozenset({"ready", "acquired", "reused"})
_GROUP_WORDS_RE = re.compile(r"\b(?:group|consolidated|company-wide)\b", re.IGNORECASE)

REASON_FIELD_UNKNOWN = "field_unknown"
REASON_NO_FINDING = "no_finding_states_the_field"
REASON_OLDER_PERIOD = "older_period"
REASON_PERIOD_UNKNOWN = "period_unknown"
REASON_SCOPE_DIFFERS = "scope_differs"
REASON_PROJECT_UNNAMED = "project_unnamed"
REASON_THIRD_PARTY_ONLY = "third_party_only"
REASON_FIELD_INFERRED = "field_inferred_from_question"
REASON_DOCUMENT_ACQUIRED = "document_acquired"
REASON_FACT_VALIDATED = "validated_fact_acquired"
REASON_ALREADY_CLOSED = "already_closed"

MAX_CLOSING_FINDINGS = 200


# ── Inputs ──────────────────────────────────────────────────────────────────── #


@dataclass(frozen=True)
class FindingFacts:
    """A finding as reconciliation reads it."""

    finding_id: str
    statement: str
    fields: tuple[str, ...]
    project: str | None = None
    question_key: str | None = None
    scope_key: str | None = None
    period_key: str | None = None
    source_kinds: tuple[str, ...] = ()
    published_at: date | None = None
    withdrawn: bool = False
    superseded_by: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "FindingFacts":
        fields, project = finding_fields(
            getattr(row, "statement", "") or "", getattr(row, "claim_key", None)
        )
        superseded = getattr(row, "superseded_by_finding_id", None)
        return cls(
            finding_id=str(row.id),
            statement=str(getattr(row, "statement", "") or ""),
            fields=fields,
            project=project,
            question_key=getattr(row, "question_key", None),
            scope_key=getattr(row, "scope_key", None),
            period_key=getattr(row, "period_key", None),
            source_kinds=tuple(getattr(row, "source_kinds_json", None) or ()),
            published_at=getattr(row, "source_published_at", None),
            withdrawn=getattr(row, "verification_status", None) == "withdrawn",
            superseded_by=str(superseded) if superseded else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "fields": list(self.fields),
            "project": self.project,
            "scope_key": self.scope_key,
            "period_key": self.period_key,
            "source_kinds": list(self.source_kinds),
            "source_published_at": self.published_at.isoformat() if self.published_at else None,
            "superseded_by_finding_id": self.superseded_by,
        }


@dataclass(frozen=True)
class GapFacts:
    gap_id: str
    gap_type: str
    description: str
    question_key: str | None = None
    status: str = ledger.GAP_OPEN
    closed_by_finding_id: str | None = None


@dataclass(frozen=True)
class FactFacts:
    """An active, validated extracted fact."""

    fact_id: str
    label: str
    field_key: str
    period: str | None = None
    scope_type: str | None = None


@dataclass(frozen=True)
class DocumentFacts:
    """A document the run holds in its corpus."""

    kind: str
    ref: str | None = None
    published: str | None = None


def finding_fields(
    statement: str, claim_key: str | None = None
) -> tuple[tuple[str, ...], str | None]:
    """``(fields, project)``: from the persisted claim key when it parses, else the text.

    The claim key is re-derived when absent (a finding written before migration 043).
    """
    parsed = rf.parse_claim_key(claim_key)
    if parsed is not None:
        return parsed
    return tuple(sorted(rf.fields_stated(statement))), rf.project_key(statement)


# ── Output ──────────────────────────────────────────────────────────────────── #


@dataclass
class GapVerdict:
    gap_id: str
    status: str
    fields: tuple[str, ...] = ()
    field_source: str | None = None
    closed_by_finding_id: str | None = None
    #: Findings that address it (fully or partly), best first.
    finding_ids: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    fact: dict[str, Any] | None = None
    documents: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "status": self.status,
            "fields": list(self.fields),
            "field_labels": [rf.label_of(f) for f in self.fields],
            "field_source": self.field_source,
            "closed_by_finding_id": self.closed_by_finding_id,
            "finding_ids": list(self.finding_ids),
            "reasons": list(self.reasons),
            "fact": self.fact,
            "documents": list(self.documents),
        }


@dataclass(frozen=True)
class Supersession:
    older_id: str
    newer_id: str
    field_key: str
    older_published_at: date
    newer_published_at: date

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.older_id,
            "superseded_by_finding_id": self.newer_id,
            "field": self.field_key,
            "source_published_at": self.older_published_at.isoformat(),
            "superseded_on": self.newer_published_at.isoformat(),
        }


@dataclass(frozen=True)
class TemporalDisagreement:
    finding_a_id: str
    finding_b_id: str
    field_key: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_a_id": self.finding_a_id,
            "finding_b_id": self.finding_b_id,
            "field": self.field_key,
            "reason": self.reason,
        }


# ── 19. Temporal supersession ───────────────────────────────────────────────── #

_SUB_PERIOD_RE = re.compile(r"\b(?:q[1-4]|h[12])\b")


def _signature(field_key: str, clause: str) -> frozenset[str]:
    """The VALUE a clause states for a field: its dates for a milestone, figures else."""
    from app.services.director.ownership import figures_in

    if field_key.startswith("milestone:"):
        return frozenset(
            {str(y) for y in rf.years_in(clause)} | set(_SUB_PERIOD_RE.findall(clause))
        )
    return figures_in(clause)


def _single_field_clause(finding: FindingFacts, field_key: str) -> str | None:
    """The clause stating ``field_key`` — only when that clause names NO other field.

    A clause stating capex and capacity together cannot say which figure is which, so it
    is never ordered in time against either (fail closed).
    """
    clause = rf.field_clause(finding.statement, field_key)
    if clause is None or rf.fields_mentioned(clause) != (field_key,):
        return None
    return clause


def supersede(
    findings: Sequence[FindingFacts],
) -> tuple[list[Supersession], list[TemporalDisagreement]]:
    """Order same-field statements in time. Pure: returns what to persist."""
    groups: dict[tuple[str, str, str, str], list[tuple[FindingFacts, frozenset[str]]]] = {}
    for finding in findings:
        if finding.withdrawn:
            continue
        for field_key in finding.fields:
            clause = _single_field_clause(finding, field_key)
            if clause is None:
                continue
            signature = _signature(field_key, clause)
            if not signature:
                continue
            key = (
                field_key,
                (finding.scope_key or "").casefold(),
                finding.project or "",
                # Different REAL periods are different facts, never one superseding the
                # other; an equal period (or none — forward guidance) is comparable.
                finding.period_key or "",
            )
            groups.setdefault(key, []).append((finding, signature))

    supersessions: list[Supersession] = []
    disagreements: list[TemporalDisagreement] = []
    for (field_key, _scope, _project, period), members in groups.items():
        if len({sig for _f, sig in members}) < 2:
            continue  # all say the same thing — restatements, not guidance changes
        if rf.is_financial_period(period):
            # Two values for ONE reporting period is a conflict to surface, never an
            # ordering by publication date.
            _pairwise_disagreements(members, field_key, "same_reporting_period", disagreements)
            continue
        dated = [(f, sig) for f, sig in members if f.published_at is not None]
        dates = [f.published_at for f, _sig in dated if f.published_at is not None]
        newest = max(dates) if dates else None
        heads = [(f, sig) for f, sig in dated if f.published_at == newest]
        if newest is None or len({sig for _f, sig in heads}) > 1:
            # No unique current statement: every differing pair is a disagreement.
            _pairwise_disagreements(members, field_key, "source_dates_unknown_or_equal",
                                    disagreements)
            continue
        current, current_sig = heads[0]
        for finding, sig in members:
            if finding.finding_id == current.finding_id or sig == current_sig:
                continue
            if finding.published_at is None:
                disagreements.append(
                    TemporalDisagreement(
                        current.finding_id, finding.finding_id, field_key,
                        "source_date_unknown",
                    )
                )
                continue
            supersessions.append(
                Supersession(
                    older_id=finding.finding_id,
                    newer_id=current.finding_id,
                    field_key=field_key,
                    older_published_at=finding.published_at,
                    newer_published_at=newest,
                )
            )
    return supersessions, disagreements


def _pairwise_disagreements(
    members: Sequence[tuple[FindingFacts, frozenset[str]]],
    field_key: str,
    reason: str,
    out: list[TemporalDisagreement],
) -> None:
    for i, (a, sig_a) in enumerate(members):
        for b, sig_b in members[i + 1:]:
            if sig_a != sig_b and a.finding_id != b.finding_id:
                out.append(TemporalDisagreement(a.finding_id, b.finding_id, field_key, reason))


# ── 18. Gap reconciliation ──────────────────────────────────────────────────── #


def _is_group_scope(scope_key: str | None) -> bool:
    from app.services.sources.fact_scope import is_group_label

    return bool(scope_key) and (scope_key == "group" or is_group_label(scope_key))


def _assess(
    finding: FindingFacts,
    *,
    gap_project: str | None,
    gap_period: str | None,
) -> list[str] | None:
    """``[]`` when the finding fully answers, reasons when partly, ``None`` when not at all."""
    reasons: list[str] = []
    same_project = rf.projects_compatible(gap_project, finding.project)
    if same_project is False:
        return None  # another project's capex says nothing about this one
    if same_project is None and gap_project:
        reasons.append(REASON_PROJECT_UNNAMED)
    if finding.scope_key and not _is_group_scope(finding.scope_key):
        # A segment's figure is not the group's; shown as partial, never a closure.
        reasons.append(REASON_SCOPE_DIFFERS)
    if gap_period:
        wanted = rf.period_rank(gap_period)
        have = rf.period_rank(finding.period_key)
        if have is None:
            reasons.append(REASON_PERIOD_UNKNOWN)
        elif wanted is not None and have < wanted:
            reasons.append(REASON_OLDER_PERIOD)
    if not (set(finding.source_kinds) & PRIMARY_SOURCE_KINDS):
        reasons.append(REASON_THIRD_PARTY_ONLY)
    return reasons


def _fact_covers(fact: FactFacts, gap_period: str | None) -> bool:
    if fact.scope_type != "group":
        return False
    if gap_period:
        wanted, have = rf.period_rank(gap_period), rf.period_rank(fact.period)
        if wanted is None or have is None or have < wanted:
            return False
    return True


def reconcile(
    gaps: Sequence[GapFacts],
    findings: Sequence[FindingFacts],
    *,
    facts: Sequence[FactFacts] = (),
    documents: Sequence[DocumentFacts] = (),
    question_fields: Mapping[str, Sequence[str]] | None = None,
) -> list[GapVerdict]:
    """One verdict per gap. Pure: the caller persists."""
    live = [f for f in findings if not f.withdrawn]
    # Current guidance before prior guidance, primary sources before third parties.
    live.sort(key=lambda f: (f.superseded_by is not None,
                             not (set(f.source_kinds) & PRIMARY_SOURCE_KINDS)))
    ready_docs: dict[str, DocumentFacts] = {}
    for doc in documents:
        ready_docs.setdefault(doc.kind, doc)
    by_question = dict(question_fields or {})
    verdicts: list[GapVerdict] = []
    for gap in gaps:
        verdicts.append(_reconcile_one(gap, live, facts, ready_docs, by_question))
    return verdicts


def _reconcile_one(
    gap: GapFacts,
    findings: Sequence[FindingFacts],
    facts: Sequence[FactFacts],
    ready_docs: Mapping[str, DocumentFacts],
    question_fields: Mapping[str, Sequence[str]],
) -> GapVerdict:
    if gap.status == ledger.GAP_CLOSED:
        return GapVerdict(
            gap.gap_id, ledger.RECONCILED_CLOSED,
            closed_by_finding_id=gap.closed_by_finding_id,
            finding_ids=[gap.closed_by_finding_id] if gap.closed_by_finding_id else [],
            reasons=[REASON_ALREADY_CLOSED],
        )
    text = gap.description or ""
    fields = rf.fields_mentioned(text)
    source = "gap_text" if fields else None
    if not fields and gap.question_key and question_fields.get(gap.question_key):
        fields = tuple(question_fields[gap.question_key])
        source = "question"

    # SUPERSEDED by a document: the gap records that a document could not be had, and
    # the run holds it. Only when the gap is about acquisition, not about a field the
    # document may still not state ("the annual report does not give capex").
    kinds = rf.document_kinds_mentioned(text)
    if kinds and (gap.gap_type in {ledger.GAP_SOURCE_UNREACHABLE, ledger.GAP_TOOL_UNAVAILABLE}
                  or source != "gap_text"):
        if all(kind in ready_docs for kind in kinds):
            return GapVerdict(
                gap.gap_id, ledger.RECONCILED_SUPERSEDED, fields=fields, field_source=source,
                reasons=[REASON_DOCUMENT_ACQUIRED],
                documents=[
                    {"kind": k, "ref": ready_docs[k].ref, "published": ready_docs[k].published}
                    for k in kinds
                ],
            )
    if not fields:
        return GapVerdict(gap.gap_id, ledger.RECONCILED_STILL_OPEN,
                          reasons=[REASON_FIELD_UNKNOWN])

    gap_project = rf.project_key(text)
    gap_period = rf.requested_period(text)
    full: dict[str, str] = {}
    partial: dict[str, tuple[str, list[str]]] = {}
    for field_key in fields:
        for finding in findings:
            if field_key not in finding.fields:
                continue
            reasons = _assess(finding, gap_project=gap_project, gap_period=gap_period)
            if reasons is None:
                continue
            if not reasons:
                full[field_key] = finding.finding_id
                break
            partial.setdefault(field_key, (finding.finding_id, reasons))
    covering_facts = {
        fact.field_key: fact for fact in facts
        if fact.field_key in fields and _fact_covers(fact, gap_period)
    }

    ordered_ids = list(dict.fromkeys(
        [full[f] for f in fields if f in full]
        + [partial[f][0] for f in fields if f in partial and f not in full]
    ))
    if len(full) == len(fields) and source == "gap_text":
        return GapVerdict(
            gap.gap_id, ledger.RECONCILED_CLOSED, fields=fields, field_source=source,
            closed_by_finding_id=full[fields[0]], finding_ids=ordered_ids,
        )
    if (
        source == "gap_text"
        and gap.gap_type in ACQUISITION_GAP_TYPES
        and all(f in full or f in covering_facts for f in fields)
    ):
        fact = next(covering_facts[f] for f in fields if f in covering_facts)
        return GapVerdict(
            gap.gap_id, ledger.RECONCILED_SUPERSEDED, fields=fields, field_source=source,
            finding_ids=ordered_ids, reasons=[REASON_FACT_VALIDATED],
            fact={"fact_id": fact.fact_id, "label": fact.label, "period": fact.period},
        )
    if full or partial or covering_facts:
        reasons = sorted({r for f in fields if f in partial and f not in full
                          for r in partial[f][1]})
        if source != "gap_text":
            reasons.insert(0, REASON_FIELD_INFERRED)
        partial_fact = next(iter(covering_facts.values()), None)
        if partial_fact is not None:
            reasons.append(REASON_FACT_VALIDATED)
        return GapVerdict(
            gap.gap_id, ledger.RECONCILED_PARTIALLY_CLOSED, fields=fields,
            field_source=source, finding_ids=ordered_ids, reasons=reasons,
            fact=({"fact_id": partial_fact.fact_id, "label": partial_fact.label,
                   "period": partial_fact.period}
                  if partial_fact is not None else None),
        )
    return GapVerdict(gap.gap_id, ledger.RECONCILED_STILL_OPEN, fields=fields,
                      field_source=source, reasons=[REASON_NO_FINDING])


# ── V2 items: missing_information and council concerns ─────────────────────── #

#: A concern is a GAP only when it says something is missing. "Capex overrun risk" is a
#: business risk and is never relabelled, whatever a finding says about capex.
_GAP_SHAPED_RE = re.compile(
    r"\b(?:not\s+(?:disclosed|provided|available|reported|stated|found|acquired|quantified|"
    r"confirmed)|undisclosed|unavailable|missing|no\s+(?:data|disclosure|figure|information|"
    r"evidence|detail)|lack\s+of|unknown|unclear|absence\s+of)\b",
    re.IGNORECASE,
)


def normalise_item_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _label_item(
    text: str, closers: Sequence[FindingFacts]
) -> dict[str, Any] | None:
    fields = rf.fields_mentioned(text)
    if not fields:
        return None
    gap = GapFacts(gap_id="", gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE, description=text)
    verdict = _reconcile_one(gap, closers, (), {}, {})
    return {
        "status": verdict.status,
        "fields": list(verdict.fields),
        "field_labels": [rf.label_of(f) for f in verdict.fields],
        "finding_ids": list(verdict.finding_ids),
        "reasons": list(verdict.reasons),
    }


def label_v2_items(
    *,
    missing_items: Iterable[Mapping[str, Any] | str],
    concerns: Iterable[Mapping[str, Any]],
    closers: Sequence[FindingFacts],
) -> dict[str, list[dict[str, Any]]]:
    """Label the V2 report's own gap statements against the V3 findings.

    Only items that name a field are labelled; everything else is left exactly as V2
    wrote it. The web drops a ``closed``/``superseded`` item and relabels a partial one.
    """
    live = sorted(
        (f for f in closers if not f.withdrawn),
        key=lambda f: (f.superseded_by is not None,
                       not (set(f.source_kinds) & PRIMARY_SOURCE_KINDS)),
    )
    missing_out: list[dict[str, Any]] = []
    for item in missing_items:
        name = item.get("field") if isinstance(item, Mapping) else item
        source = item.get("source") if isinstance(item, Mapping) else None
        if not name:
            continue
        labelled = _label_item(str(name), live)
        if labelled is not None:
            missing_out.append({"field": str(name), "source": source, **labelled})
    concerns_out: list[dict[str, Any]] = []
    for concern in concerns:
        text = str(concern.get("text") or "")
        if not text or not _GAP_SHAPED_RE.search(text):
            continue
        labelled = _label_item(text, live)
        if labelled is not None:
            concerns_out.append({
                "text": text,
                "key": normalise_item_text(text),
                "agent": concern.get("agent"),
                **labelled,
            })
    return {"missing_information": missing_out[:80], "council_concerns": concerns_out[:80]}


# ── Running it over one ledger run ─────────────────────────────────────────── #


def ready_documents(
    core_filings: Mapping[str, Any] | None, core_disclosures: Mapping[str, Any] | None
) -> list[DocumentFacts]:
    """The documents the run HOLDS, from what the pre-run acquisition reported."""
    out: list[DocumentFacts] = []
    slot_kind = {"annual": rf.DOC_ANNUAL, "quarterly": rf.DOC_INTERIM}
    for slot, kind in slot_kind.items():
        item = (core_filings or {}).get(slot) or {}
        if isinstance(item, Mapping) and item.get("state") in _READY_STATES:
            out.append(DocumentFacts(kind=kind, ref=item.get("form"),
                                     published=item.get("filing_date")))
    for item in (core_disclosures or {}).get("documents") or []:
        if not isinstance(item, Mapping) or item.get("state") != "ready":
            continue
        kind = str(item.get("document_kind") or "")
        if kind in {rf.DOC_ANNUAL, rf.DOC_INTERIM, rf.DOC_RESULTS}:
            out.append(DocumentFacts(kind=kind, ref=item.get("document_ref") or item.get("id"),
                                     published=item.get("filing_date")))
    return out


def question_field_map(question_rows: Iterable[Any]) -> dict[str, tuple[str, ...]]:
    """Fields each question asks for: its required metrics, then its own words."""
    out: dict[str, tuple[str, ...]] = {}
    for row in question_rows:
        fields = list(rf.fields_for_metrics(getattr(row, "required_metrics_json", None) or ()))
        for f in rf.fields_mentioned(getattr(row, "text", "") or ""):
            if f not in fields:
                fields.append(f)
        if fields:
            out[str(row.question_key)] = tuple(fields)
    return out


async def _validated_facts(session: Any, company_id: Any) -> list[FactFacts]:
    from sqlalchemy import select

    from app.models.extracted_document import ExtractedDocument, ExtractedFact
    from app.services.sources.company_documents import company_documents_clause

    labels = sorted({label for f in rf.FIELDS for label in f.fact_labels})
    rows = (
        await session.execute(
            select(ExtractedFact)
            .join(ExtractedDocument, ExtractedDocument.id == ExtractedFact.extracted_document_id)
            .where(
                company_documents_clause(company_id),
                ExtractedFact.is_active.is_(True),
                ExtractedFact.validation_status == "validated",
                ExtractedFact.label.in_(labels),
            )
            .order_by(ExtractedFact.label, ExtractedFact.period.desc())
            .limit(200)
        )
    ).scalars().all()
    out: list[FactFacts] = []
    for row in rows:
        field_key = rf.field_for_fact_label(row.label)
        if field_key:
            out.append(FactFacts(str(row.id), row.label, field_key, row.period, row.scope_type))
    return out


async def reconcile_run(
    session: Any,
    run: Any,
    *,
    company_id: Any = None,
    core_filings: Mapping[str, Any] | None = None,
    core_disclosures: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Supersede, reconcile, persist. Returns the record the report carries."""
    from sqlalchemy import select

    from app.models.ledger import (
        ResearchDisagreement,
        ResearchFinding,
        ResearchGap,
        ResearchQuestion,
    )

    finding_rows = list((await session.execute(
        select(ResearchFinding).where(ResearchFinding.research_run_id == run.id)
    )).scalars().all())
    by_id = {str(row.id): row for row in finding_rows}

    # 19 — supersession first, so closure prefers current guidance.
    supersessions, temporal = supersede([FindingFacts.from_row(r) for r in finding_rows])
    for s in supersessions:
        await ledger.mark_superseded(session, by_id[s.older_id], by=by_id[s.newer_id])
    existing_pairs = {
        frozenset((str(a), str(b)))
        for a, b in (await session.execute(
            select(ResearchDisagreement.finding_a_id, ResearchDisagreement.finding_b_id)
            .where(ResearchDisagreement.research_run_id == run.id)
        )).all()
    }
    recorded_disagreements = 0
    for d in temporal:
        pair = frozenset((d.finding_a_id, d.finding_b_id))
        if pair in existing_pairs:
            continue
        existing_pairs.add(pair)
        await ledger.record_disagreement(
            session, run, finding_a=by_id[d.finding_a_id], finding_b=by_id[d.finding_b_id],
            nature="value",
            description=(
                f"Two statements of {rf.label_of(d.field_key)} for the same scope and "
                f"project differ, and their sources cannot be ordered in time "
                f"({d.reason.replace('_', ' ')}). Neither is treated as current."
            ),
        )
        recorded_disagreements += 1

    findings = [FindingFacts.from_row(r) for r in finding_rows]
    gap_rows = list((await session.execute(
        select(ResearchGap).where(ResearchGap.research_run_id == run.id)
        .order_by(ResearchGap.created_at)
    )).scalars().all())
    question_rows = (await session.execute(
        select(ResearchQuestion).where(ResearchQuestion.research_run_id == run.id)
    )).scalars().all()
    facts: list[FactFacts] = []
    if company_id is not None:
        try:
            async with session.begin_nested():
                facts = await _validated_facts(session, company_id)
        except Exception:  # noqa: BLE001 - no facts is the fail-closed answer
            facts = []
    verdicts = reconcile(
        [
            GapFacts(
                gap_id=str(row.id), gap_type=row.gap_type, description=row.description,
                question_key=row.question_key, status=row.status,
                closed_by_finding_id=(str(row.closed_by_finding_id)
                                      if row.closed_by_finding_id else None),
            )
            for row in gap_rows
        ],
        findings,
        facts=facts,
        documents=ready_documents(core_filings, core_disclosures),
        question_fields=question_field_map(question_rows),
    )
    gaps_by_id = {str(row.id): row for row in gap_rows}
    counts: dict[str, int] = {}
    for verdict in verdicts:
        counts[verdict.status] = counts.get(verdict.status, 0) + 1
        row = gaps_by_id[verdict.gap_id]
        closer = by_id.get(verdict.closed_by_finding_id or "")
        if verdict.status == ledger.RECONCILED_CLOSED and closer is None:
            verdict.status = ledger.RECONCILED_STILL_OPEN  # never closed without a row
        await ledger.reconcile_gap(
            session, row, status=verdict.status, detail=verdict.to_dict(),
            finding=closer if verdict.status == ledger.RECONCILED_CLOSED else None,
        )

    closers = [f for f in findings if f.fields and not f.withdrawn]
    return {
        "version": 1,
        "gaps": [v.to_dict() for v in verdicts][:120],
        "counts": dict(sorted(counts.items())),
        "supersessions": [s.to_dict() for s in supersessions][:60],
        "temporal_disagreements": [d.to_dict() for d in temporal][:60],
        "temporal_disagreements_recorded": recorded_disagreements,
        # What attach_to_report labels the V2 report's own gap statements against.
        "closing_findings": [f.to_dict() for f in closers][:MAX_CLOSING_FINDINGS],
        "note": (
            "Each gap was reconciled against every finding, validated fact and acquired "
            "document of the run. Closed and superseded gaps are not shown as open; a "
            "partially closed gap names the finding that addresses it."
        ),
    }


def closers_from_payload(items: Iterable[Mapping[str, Any]]) -> list[FindingFacts]:
    """Rebuild the closing findings from the stored record (for the V2 labelling)."""
    out: list[FindingFacts] = []
    for item in items:
        fields = tuple(f for f in (item.get("fields") or ()) if rf.is_known_field(f))
        if not fields or not item.get("finding_id"):
            continue
        out.append(FindingFacts(
            finding_id=str(item["finding_id"]),
            statement="",
            fields=fields,
            project=item.get("project"),
            scope_key=item.get("scope_key"),
            period_key=item.get("period_key"),
            source_kinds=tuple(item.get("source_kinds") or ()),
            published_at=rf.parse_date(item.get("source_published_at")),
            superseded_by=item.get("superseded_by_finding_id"),
        ))
    return out


__all__ = [
    "ACQUISITION_GAP_TYPES",
    "ISSUER_SOURCE_KINDS",
    "PRIMARY_SOURCE_KINDS",
    "DocumentFacts",
    "FactFacts",
    "FindingFacts",
    "GapFacts",
    "GapVerdict",
    "Supersession",
    "TemporalDisagreement",
    "closers_from_payload",
    "finding_fields",
    "label_v2_items",
    "normalise_item_text",
    "question_field_map",
    "ready_documents",
    "reconcile",
    "reconcile_run",
    "supersede",
]
