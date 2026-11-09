"""ADR-094 — every gap carries the canonical resolution state research derived for it.

THE DEFECT
==========
Every report of the 2026-11 round-3 reruns listed 14–27 "not found" gaps over documents
the platform HELD (JPM3: "No reported FY2025 CET1 ratio for the Firm" beside a held 10-Q
of 739 chunks that states CET1 14.0%). The re-read (ADR-084/090) ran 23–29 queries per
report, yet:

* a gap the investigator wrote AFTER its question's re-read never entered a re-read of
  its own: the re-read is chosen from the QUESTION's topics before the model answers,
  and a question whose count contract was met (JPM ``capital_adequacy``, satisfied by a
  subsidiary's CET1 figure) never re-read at all;
* reconciliation knew a dozen research fields (capex, cash, revenue …), so every other
  gap — CET1, litigation, buybacks, segment splits — was ``field_unknown`` and rendered
  as a plain absence ("Not acquired by the platform");
* nothing told "searched and not there" from "never searched", "the page holding it was
  not parsed" or "held, but not read into a finding".

WHAT THIS MODULE IS
===================
A deterministic queue that runs once per run, after reconciliation (ADR-086/091) and
before the Chair. Every gap the ledger holds is routed exactly one way (spec §6):

* (A) ``mapped_existing`` — reconciliation found a compatible finding (CLOSED,
  SUPERSEDED, PARTIALLY_CLOSED);
* (B) ``reread`` — the gap's OWN words name a metric or topic of the reviewed lexicon
  (``document_reread.TOPICS``), so the subject's held PRIMARY documents are searched
  for it: corpus lexical search, primary documents only, no web, no model;
* (C) ``not_required`` — with the reason: not an absence (a conflict, a scope or
  entity question), a source that was blocked, an answer only a non-issuer source can
  give (a peer, a market size, a transcript), no searchable topic in its words, no
  primary document held.

and ends with one RESULT in a closed vocabulary (spec §5). Only
``NOT_FOUND_AFTER_SEARCH`` — the held documents were searched for the gap's topic and no
passage carries it — may be rendered as "not found in the available evidence"; every
other result has its own wording (``RESULT_WORDS``).

NOTHING HERE WRITES A FINDING
=============================
A passage the re-read finds is not a verified finding: no model read it, no figure was
checked. It changes what the gap SAYS — "a held document (10-Q · 2026-10-30) has a
passage on the CET1 ratio that was not read into a finding" instead of "not found" —
never what the report claims about the company. Missing data is acceptable; wrong data
is not.

Bounded: ``MAX_QUERIES_PER_GAP`` per gap, ``MAX_RUN_QUERIES`` per run (identical queries
are run once and shared), ``TOP_K`` hits each. Recorded: every gap's queries, documents
searched and result, and the counts the canonical state and the admin page show.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.services import research_fields as rf
from app.services.agents import document_reread as dr
from app.services.ledger import store as ledger

logger = logging.getLogger(__name__)

RESOLUTION_VERSION = 1

# ── The result vocabulary (spec §5) ─────────────────────────────────────────── #

RESULT_FOUND = "FOUND"
RESULT_PARTIALLY_FOUND = "PARTIALLY_FOUND"
RESULT_NOT_FOUND_AFTER_SEARCH = "NOT_FOUND_AFTER_SEARCH"
RESULT_DOCUMENT_NOT_PARSED = "DOCUMENT_NOT_PARSED"
RESULT_EXTRACTION_FAILED = "EXTRACTION_FAILED"
RESULT_SCOPE_MISMATCH = "SCOPE_MISMATCH"
RESULT_PERIOD_MISMATCH = "PERIOD_MISMATCH"
RESULT_SOURCE_BLOCKED = "SOURCE_BLOCKED"
RESULT_NOT_RUN = "NOT_RUN"
RESULTS: tuple[str, ...] = (
    RESULT_FOUND, RESULT_PARTIALLY_FOUND, RESULT_NOT_FOUND_AFTER_SEARCH,
    RESULT_DOCUMENT_NOT_PARSED, RESULT_EXTRACTION_FAILED, RESULT_SCOPE_MISMATCH,
    RESULT_PERIOD_MISMATCH, RESULT_SOURCE_BLOCKED, RESULT_NOT_RUN,
)
#: The ONLY result that may be worded as an absence in the evidence.
ABSENCE_RESULTS: frozenset[str] = frozenset({RESULT_NOT_FOUND_AFTER_SEARCH})

#: How each result is worded wherever a gap is shown (page, chair input, report). A
#: ``{document}`` / ``{topic}`` placeholder is filled from platform-derived fields only.
#: ADR-094 review — a NARRATIVE topic (legal proceedings, guidance …) is located by being
#: named, so its wording claims no figure.
EXTRACTION_FAILED_NARRATIVE_WORDS = (
    "Not established either way: {document} has a passage naming {topic} that no finding "
    "has read; whether it answers this gap is unchecked."
)
RESULT_WORDS: dict[str, str] = {
    RESULT_FOUND: "Answered by a finding in this report.",
    RESULT_PARTIALLY_FOUND: "Partly answered by a finding in this report.",
    RESULT_NOT_FOUND_AFTER_SEARCH: (
        "Not found in the available evidence: the held primary documents were searched "
        "for {topic} and no passage states it."
    ),
    RESULT_DOCUMENT_NOT_PARSED: (
        "Not established: part of a held document ({document}) was not parsed, so the "
        "research cannot say it is absent."
    ),
    RESULT_EXTRACTION_FAILED: (
        "Not established either way: {document} has a passage naming {topic} beside a "
        "figure that no finding has read; whether it answers this gap is unchecked."
    ),
    RESULT_SCOPE_MISMATCH: (
        "Only another scope's figures were found (e.g. a segment's); the scope asked for "
        "is not established."
    ),
    RESULT_PERIOD_MISMATCH: (
        "Only figures for another or an unconfirmed period were found; the period asked "
        "for is not established."
    ),
    RESULT_SOURCE_BLOCKED: "The source could not be reached; not established.",
    RESULT_NOT_RUN: "Not searched for in the held primary documents ({why}); not established.",
}

# ── Routes (spec §6) ─────────────────────────────────────────────────────────── #

ROUTE_MAPPED_EXISTING = "mapped_existing"
ROUTE_REREAD = "reread"
ROUTE_NOT_REQUIRED = "not_required"

#: Why a gap needs no re-read, or why one could not run. Closed vocabulary.
WHY_NOT_ABSENCE = "not_an_absence_gap"
WHY_SOURCE_BLOCKED = "source_blocked"
WHY_NON_ISSUER = "needs_a_non_issuer_source"
WHY_NO_TOPIC = "no_searchable_topic_in_gap"
WHY_NO_DOCUMENT = "no_primary_document_held"
WHY_NO_CORPUS = "corpus_search_unavailable"
WHY_BUDGET = "reread_query_budget_spent"
WHY_SEARCH_FAILED = "search_failed"
#: ADR-094 review — some of the gap's planned queries did not run (the per-gap or the
#: run's query cap), so not every topic it names was searched.
WHY_INCOMPLETE = "not_every_query_ran"
#: The queue's own wall-clock budget was spent before this gap was searched.
WHY_DEADLINE = "gap_queue_time_budget_spent"
WHY_WORDS: dict[str, str] = {
    WHY_NOT_ABSENCE: "it is not about a missing figure",
    WHY_SOURCE_BLOCKED: "the source could not be reached",
    WHY_NON_ISSUER: "it needs a source other than the issuer's own documents",
    WHY_NO_TOPIC: "it names no metric or topic the platform has a reviewed vocabulary for",
    WHY_NO_DOCUMENT: "no primary document of the company is held",
    WHY_NO_CORPUS: "the document search was unavailable",
    WHY_BUDGET: "the run's re-read budget was spent",
    WHY_SEARCH_FAILED: "the document search failed",
    WHY_INCOMPLETE: "not every search it needed could run",
    WHY_DEADLINE: "the run's time budget for re-reads was spent",
}

#: Gap types that say something is ABSENT (the only ones re-read).
ABSENCE_GAP_TYPES: frozenset[str] = frozenset({
    ledger.GAP_EVIDENCE_UNAVAILABLE, ledger.GAP_PERIOD_MISSING,
    ledger.GAP_TRANSCRIPT_UNAVAILABLE, ledger.GAP_BUDGET_EXHAUSTED,
})
BLOCKED_GAP_TYPES: frozenset[str] = frozenset({
    ledger.GAP_SOURCE_UNREACHABLE, ledger.GAP_TOOL_UNAVAILABLE,
})

#: Bounds. A re-read query is one lexical corpus search (no web, no model).
MAX_QUERIES_PER_GAP = 3
MAX_RUN_QUERIES = 80
TOP_K = 30
MAX_EVIDENCE_PER_GAP = 3
MAX_RECORDED = 120
#: ADR-094 review — the whole queue's wall-clock budget (step 7c). A gap not reached in
#: time is NOT_RUN (``WHY_DEADLINE``), never "not found".
MAX_QUEUE_SECONDS = 90.0

#: What only a NON-issuer source answers: a peer's figures, a market size, a third
#: party's view, a transcript, a policy rate. Bounded; literal alternation only.
_NON_ISSUER_RE = re.compile(
    r"\b(?:third[- ]party|independent|peers?|competitors?|rivals?|market[ \t]share|"
    r"market[ \t]siz\w{0,6}|industry[ \t](?:data|aggregates?|benchmarks?|figures?)|"
    r"consensus|analysts?|transcripts?|earnings[ \t]call|benchmark[ \t]rates?|"
    r"policy[- ]rates?|central[ \t]bank|macro\w{0,9}|shareholder[ \t]register|"
    r"substantial[ \t](?:holders?|shareholders?)|major[- ]holders?)\b",
    re.IGNORECASE,
)


# ── Inputs ──────────────────────────────────────────────────────────────────── #


@dataclass(frozen=True)
class GapInput:
    """One ledger gap, with what reconciliation decided about it."""

    gap_id: str
    gap_type: str
    description: str
    question_key: str | None = None
    status: str = ledger.GAP_OPEN
    #: ``gap_reconciliation`` verdict: ``closed`` / ``partially_closed`` / … and the
    #: report status (``CLOSED`` / ``NONCOMPARABLE`` / …), with its reasons.
    reconciliation_status: str | None = None
    gap_status: str | None = None
    reasons: tuple[str, ...] = ()
    finding_ids: tuple[str, ...] = ()


@dataclass
class GapPlan:
    gap_id: str
    route: str
    topics: tuple[str, ...] = ()
    period: str | None = None
    scope: str | None = None
    why: str | None = None
    queries: list[str] = field(default_factory=list)


@dataclass
class GapResolution:
    gap_id: str
    question_key: str | None
    route: str
    result: str
    topics: tuple[str, ...] = ()
    period: str | None = None
    scope: str | None = None
    why: str | None = None
    #: The producer that wrote the gap (``investigator``: the ledger gap rows).
    producer: str = "investigator"
    source_classes_searched: tuple[str, ...] = ()
    documents_searched: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    #: Passages the re-read located: document name (platform-derived), evidence id, page.
    evidence: list[dict[str, Any]] = field(default_factory=list)
    finding_ids: tuple[str, ...] = ()
    #: Held documents whose parse is known to be truncated (``pages_total`` > parsed).
    unparsed_documents: list[str] = field(default_factory=list)
    #: ADR-094 review — this gap's query accounting: planned, run (or answered from the
    #: shared cache), failed, skipped by a cap or the deadline.
    queries_planned: int = 0
    queries_succeeded: int = 0
    queries_failed: int = 0
    queries_skipped: int = 0

    @property
    def words(self) -> str:
        return render_words(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "question_key": self.question_key,
            "producer": self.producer,
            "route": self.route,
            "result": self.result,
            "why": self.why,
            "topics": list(self.topics),
            "topic_labels": [dr.label_of(t) for t in self.topics],
            "period_constraint": self.period,
            "scope_constraint": self.scope,
            "source_classes_searched": list(self.source_classes_searched),
            "documents_searched": list(self.documents_searched)[:8],
            "queries": list(self.queries),
            "evidence": list(self.evidence)[:MAX_EVIDENCE_PER_GAP],
            "finding_ids": list(self.finding_ids)[:4],
            "unparsed_documents": list(self.unparsed_documents)[:4],
            "queries_planned": self.queries_planned,
            "queries_succeeded": self.queries_succeeded,
            "queries_failed": self.queries_failed,
            "queries_skipped": self.queries_skipped,
            "words": self.words,
            "may_say_not_found": self.result in ABSENCE_RESULTS,
        }


def render_words(res: "GapResolution | Mapping[str, Any]") -> str:
    """The honest wording of one result. Placeholders take platform-derived values only
    (a document's form/kind/date, a lexicon label, a closed reason) — never page text."""
    get = res.get if isinstance(res, Mapping) else (lambda k, d=None: getattr(res, k, d))
    result = str(get("result") or RESULT_NOT_RUN)
    topics = list(get("topics") or ())
    evidence = list(get("evidence") or ())
    if result == RESULT_EXTRACTION_FAILED and evidence and isinstance(evidence[0], Mapping):
        # What the located passage CARRIES — not every topic the gap's words named.
        topics = [t for t in evidence[0].get("topics") or () if isinstance(t, str)] or topics
    topic = " / ".join(dr.label_of(t) for t in topics[:2]) or "it"
    unparsed = list(get("unparsed_documents") or ())
    document = (
        str(evidence[0].get("document")) if evidence and isinstance(evidence[0], Mapping)
        else (str(unparsed[0]) if unparsed else "a held document")
    )
    why = WHY_WORDS.get(str(get("why") or ""), "no re-read was run")
    template = RESULT_WORDS.get(result, RESULT_WORDS[RESULT_NOT_RUN])
    if result == RESULT_EXTRACTION_FAILED and topics and all(
        not getattr(dr.TOPICS_BY_KEY.get(t), "needs_figure", True) for t in topics[:2]
    ):
        template = EXTRACTION_FAILED_NARRATIVE_WORDS
    return template.format(topic=topic, document=document, why=why)


# ── Planning (pure) ─────────────────────────────────────────────────────────── #


def gap_topics(description: str | None) -> tuple[str, ...]:
    """The lexicon topics the gap's OWN words name or ask for — never its question's: a
    gap under a revenue question about customer concentration is about concentration."""
    return dr.topics_asked(description)


def plan_gap(gap: GapInput) -> GapPlan:
    """Route one gap (spec §6). Pure."""
    text = gap.description or ""
    topics = gap_topics(text)
    plan = GapPlan(gap.gap_id, ROUTE_NOT_REQUIRED, topics=topics,
                   period=rf.requested_period(text), scope=_gap_scope(text))
    hidden = gap.status == ledger.GAP_CLOSED or gap.reconciliation_status in {
        ledger.RECONCILED_CLOSED, ledger.RECONCILED_SUPERSEDED
    }
    partial = gap.reconciliation_status == ledger.RECONCILED_PARTIALLY_CLOSED and (
        gap.gap_status in (None, "PARTIALLY_CLOSED")
    )
    if hidden or partial:
        plan.route = ROUTE_MAPPED_EXISTING
        return plan
    if gap.gap_type in BLOCKED_GAP_TYPES:
        plan.why = WHY_SOURCE_BLOCKED
        return plan
    if gap.gap_type not in ABSENCE_GAP_TYPES:
        plan.why = WHY_NOT_ABSENCE
        return plan
    if _NON_ISSUER_RE.search(text[:2000]):
        plan.why = WHY_NON_ISSUER
        return plan
    if not topics:
        plan.why = WHY_NO_TOPIC
        return plan
    plan.route = ROUTE_REREAD
    plan.queries = reread_queries(topics)
    return plan


def reread_queries(topics: Sequence[str], limit: int = MAX_QUERIES_PER_GAP) -> list[str]:
    """The bounded lexical queries for a gap's topics (``document_reread.reread_plan``'s
    reduction: distinctive terms only — the index has no stopword list)."""
    from app.services.corpus.query_terms import keyword_query

    out: list[str] = []
    # Every topic's own query first, so a cap never leaves a topic unsearched while a
    # row-label query of another runs; the row-label queries only with room left.
    passes = [[raw for raw in dr.topic_queries(t)[:1]] for t in topics
              if t in dr.TOPICS_BY_KEY]
    passes += [[raw for raw in dr.topic_queries(t)[1:]] for t in topics
               if t in dr.TOPICS_BY_KEY]
    for raws in passes:
        for raw in raws:
            query = keyword_query(raw) or raw
            if query and query not in out:
                out.append(query)
            if len(out) >= limit:
                return out
    return out


def topics_searched(topics: Sequence[str], queries: Sequence[str]) -> bool:
    """Whether every topic got its own query among ``queries`` (the ones that RAN)."""
    from app.services.corpus.query_terms import keyword_query

    ran = set(queries)
    for topic in topics:
        if topic not in dr.TOPICS_BY_KEY:
            continue
        raw = dr.topic_queries(topic)[0]
        if (keyword_query(raw) or raw) not in ran:
            return False
    return True


def _gap_scope(text: str) -> str | None:
    from app.services.pipeline.gap_reconciliation import gap_scope

    return gap_scope(text)


# ── Classification (pure) ───────────────────────────────────────────────────── #


def _hit_year_ok(
    hit: Mapping[str, Any], period: str | None, topics: Sequence[str] = ()
) -> bool | None:
    """``True`` when a hit is (or may be) for the gap's period, ``False`` when it is for
    a different one, ``None`` when the gap names no period."""
    wanted = rf.period_rank(period)
    if wanted is None:
        return None
    have = rf.period_rank(str(hit.get("period_key") or "") or None)
    if have is not None:
        return have[0] == wanted[0]
    # ADR-094 review — an undated chunk counts for the period only when the year is
    # stated BESIDE the carried metric, not anywhere in the passage.
    year = str(wanted[0])
    windows = dr.carried_windows(str(hit.get("text") or "")[:8000], topics or ())
    return any(year in w for w in windows)


def _segment_scoped(hit: Mapping[str, Any]) -> bool:
    scope = str(hit.get("scope_type") or "").lower()
    return scope in {"segment", "business_unit", "division", "brand", "product_line"}


def truncated_documents(inventory: Iterable[Mapping[str, Any]] | None) -> list[str]:
    """Held primary documents whose parse is known to be TRUNCATED (ADR-094 §8): the
    parse stopped before the document's last page, or the extractor said so."""
    out: list[str] = []
    for doc in inventory or ():
        if not isinstance(doc, Mapping):
            continue
        total = int(doc.get("pages_total") or 0)
        parsed = int(doc.get("pages_parsed") or doc.get("pages") or 0)
        if doc.get("truncated") or (total and parsed and parsed < total):
            name = dr.document_name(doc)
            if total and parsed and parsed < total:
                name = f"{name}, {parsed} of {total} pages parsed"
            if name not in out:
                out.append(name)
    return out


def classify(
    plan: GapPlan,
    gap: GapInput,
    *,
    hits: Sequence[Mapping[str, Any]],
    searched: bool,
    inventory: Sequence[Mapping[str, Any]] | None,
    why_not_run: str | None = None,
    complete: bool = True,
    why_incomplete: str | None = None,
) -> GapResolution:
    """One gap's result. Pure.

    ``complete``: every planned query ran without error and every topic got its own
    query. Only a complete search may end NOT_FOUND_AFTER_SEARCH."""
    res = GapResolution(
        gap_id=gap.gap_id, question_key=gap.question_key, route=plan.route,
        result=RESULT_NOT_RUN, topics=plan.topics, period=plan.period, scope=plan.scope,
        why=plan.why, finding_ids=gap.finding_ids, queries=list(plan.queries),
    )
    if plan.route == ROUTE_MAPPED_EXISTING:
        res.result = (
            RESULT_PARTIALLY_FOUND
            if gap.reconciliation_status == ledger.RECONCILED_PARTIALLY_CLOSED
            else RESULT_FOUND
        )
        return res
    if plan.route == ROUTE_NOT_REQUIRED:
        res.result = RESULT_SOURCE_BLOCKED if plan.why == WHY_SOURCE_BLOCKED else (
            _mismatch_from_reconciliation(gap) or RESULT_NOT_RUN
        )
        return res
    docs = [d for d in inventory or () if isinstance(d, Mapping)]
    res.documents_searched = [dr.document_name(d) for d in docs
                              if int(d.get("indexed_chunks") or 0)][:8]
    res.unparsed_documents = truncated_documents(docs)
    if not searched:
        res.why = why_not_run or WHY_NO_CORPUS
        mismatch = _mismatch_from_reconciliation(gap)
        if mismatch:
            res.result = mismatch
            return res
        if res.why == WHY_NO_DOCUMENT and docs:
            # Held, but nothing of it is indexed: the document was not parsed.
            res.result = RESULT_DOCUMENT_NOT_PARSED
            res.unparsed_documents = [dr.document_name(d) for d in docs][:4]
        return res
    res.source_classes_searched = ("issuer_primary_documents",)
    carrying = [
        h for h in hits
        if not dr.is_boilerplate(h.get("text"))
        and dr.metrics_carried(str(h.get("text") or ""), plan.topics)
    ]
    if carrying:
        in_period = [h for h in carrying
                     if _hit_year_ok(h, plan.period, plan.topics) is not False]
        group_ok = [h for h in in_period if not (plan.scope == "group" and _segment_scoped(h))]
        best = group_ok or in_period or carrying
        res.evidence = [_evidence_of(h, plan.topics, plan.period)
                        for h in best[:MAX_EVIDENCE_PER_GAP]]
        if group_ok:
            res.result = RESULT_EXTRACTION_FAILED
        elif in_period:
            res.result = RESULT_SCOPE_MISMATCH
        else:
            res.result = RESULT_PERIOD_MISMATCH
        return res
    mismatch = _mismatch_from_reconciliation(gap)
    if mismatch:
        # A finding speaks to the metric for another period or scope: that is what the
        # gap says, whether or not part of a document went unparsed.
        res.result = mismatch
        return res
    if not complete:
        # ADR-094 review — part of the search did not run: an absence cannot be stated.
        res.result = RESULT_NOT_RUN
        res.why = why_incomplete or WHY_INCOMPLETE
        return res
    if res.unparsed_documents:
        # Searched, not found — but part of a held document was never parsed, so its
        # absence cannot be stated (spec §8).
        res.result = RESULT_DOCUMENT_NOT_PARSED
        return res
    res.result = RESULT_NOT_FOUND_AFTER_SEARCH
    return res


def _mismatch_from_reconciliation(gap: GapInput) -> str | None:
    """A NONCOMPARABLE verdict: a finding speaks to the metric, for another period or
    scope. The re-read found nothing better, so that is what the gap says."""
    if gap.gap_status != "NONCOMPARABLE":
        return None
    from app.services.pipeline import gap_reconciliation as gr

    reasons = set(gap.reasons)
    if reasons & {gr.REASON_SCOPE_DIFFERS, gr.REASON_SUBJECT_UNCERTAIN}:
        return RESULT_SCOPE_MISMATCH
    return RESULT_PERIOD_MISMATCH


def _evidence_of(
    hit: Mapping[str, Any], topics: Sequence[str], period: str | None = None
) -> dict[str, Any]:
    """Platform-derived fields only: never the passage text (untrusted, and not a
    verified figure). ``period``: the chunk's own period key, else the gap's period when
    the passage states its year beside the metric, else ``None`` (unknown)."""
    own = str(hit.get("period_key") or "") or None
    confirmed = period if (own is None and _hit_year_ok(hit, period, topics)) else None
    return {
        "period": own or confirmed,
        "document": dr.document_name({
            "form": hit.get("form"), "document_type": hit.get("document_type"),
            "published_at": hit.get("published_at"),
        }),
        "evidence_id": str(hit.get("evidence_id") or "") or None,
        "page": hit.get("page_start"),
        "table": bool(dr.looks_like_table(hit)),
        "topics": sorted(dr.metrics_carried(str(hit.get("text") or ""), topics)),
    }


# ── Counts (spec §6) ────────────────────────────────────────────────────────── #


def counts_of(resolutions: Sequence[GapResolution], *, queries_run: int) -> dict[str, Any]:
    by_result: dict[str, int] = {}
    for res in resolutions:
        by_result[res.result] = by_result.get(res.result, 0) + 1
    reread = [r for r in resolutions if r.route == ROUTE_REREAD]
    located = {RESULT_EXTRACTION_FAILED, RESULT_PERIOD_MISMATCH, RESULT_SCOPE_MISMATCH}
    return {
        "gaps_created": len(resolutions),
        "gaps_mapped_existing": sum(1 for r in resolutions if r.route == ROUTE_MAPPED_EXISTING),
        "gaps_reread": sum(1 for r in reread if r.source_classes_searched),
        # ANSWERED by a finding of this report (mapped). Never a re-read passage.
        "gaps_found": sum(
            1 for r in resolutions if r.result in {RESULT_FOUND, RESULT_PARTIALLY_FOUND}
        ),
        # A held passage the re-read located that no finding has read: unverified.
        "gaps_located_unverified": sum(
            1 for r in reread if r.result in located and r.evidence
        ),
        # Re-read and nothing located.
        "gaps_failed": sum(
            1 for r in reread if r.source_classes_searched and not r.evidence
        ),
        "gaps_not_run": by_result.get(RESULT_NOT_RUN, 0),
        "gaps_not_required": sum(1 for r in resolutions if r.route == ROUTE_NOT_REQUIRED),
        "by_result": dict(sorted(by_result.items())),
        "reread_queries": queries_run,
        "reread_query_cap": MAX_RUN_QUERIES,
        # ADR-094 review — the re-read's query accounting (per gap, summed).
        "queries_planned": sum(r.queries_planned for r in reread),
        "queries_succeeded": sum(r.queries_succeeded for r in reread),
        "queries_failed": sum(r.queries_failed for r in reread),
        "queries_skipped": sum(r.queries_skipped for r in reread),
    }


# ── The run (I/O) ───────────────────────────────────────────────────────────── #


def gap_input_of(row: Any) -> GapInput:
    detail = getattr(row, "reconciliation_json", None) or {}
    return GapInput(
        gap_id=str(row.id),
        gap_type=str(row.gap_type or ""),
        description=str(row.description or ""),
        question_key=getattr(row, "question_key", None),
        status=str(getattr(row, "status", "") or ledger.GAP_OPEN),
        reconciliation_status=getattr(row, "reconciliation_status", None),
        gap_status=detail.get("gap_status") if isinstance(detail, Mapping) else None,
        reasons=tuple(str(r) for r in (detail.get("reasons") or ())) if isinstance(
            detail, Mapping) else (),
        finding_ids=tuple(str(f) for f in (detail.get("finding_ids") or ())) if isinstance(
            detail, Mapping) else (),
    )


async def resolve_gaps(
    gaps: Sequence[GapInput],
    *,
    inventory: Sequence[Mapping[str, Any]] | None,
    search: Any,
    max_run_queries: int = MAX_RUN_QUERIES,
    deadline: float | None = None,
) -> tuple[list[GapResolution], int]:
    """Plan, search and classify every gap. ``search(query) -> list[dict] | None`` is
    awaited once per DISTINCT query (``None``: the search failed). ``deadline`` is a
    ``time.monotonic()`` instant: a gap not searched by then is NOT_RUN. Never raises."""
    docs = [d for d in inventory or () if isinstance(d, Mapping)]
    indexed = any(int(d.get("indexed_chunks") or 0) for d in docs)
    cache: dict[str, list[dict[str, Any]] | None] = {}
    queries_run = 0
    out: list[GapResolution] = []
    for gap in gaps:
        plan = plan_gap(gap)
        if plan.route != ROUTE_REREAD:
            out.append(classify(plan, gap, hits=(), searched=False, inventory=docs))
            continue
        if inventory is None or search is None:
            out.append(classify(plan, gap, hits=(), searched=False, inventory=docs,
                                why_not_run=WHY_NO_CORPUS))
            continue
        if not indexed:
            out.append(classify(plan, gap, hits=(), searched=False, inventory=docs,
                                why_not_run=WHY_NO_DOCUMENT))
            continue
        hits: list[dict[str, Any]] = []
        succeeded: list[str] = []
        failed = 0
        skipped = 0
        timed_out = False
        for query in plan.queries:
            if query not in cache:
                if deadline is not None and time.monotonic() > deadline:
                    skipped += 1
                    timed_out = True
                    continue
                if queries_run >= max_run_queries:
                    skipped += 1
                    continue
                queries_run += 1
                try:
                    cache[query] = await search(query)
                except Exception:  # noqa: BLE001 - one query costs itself only
                    cache[query] = None
            result = cache[query]
            if result is None:
                failed += 1
                continue
            succeeded.append(query)
            hits.extend(result)
        # Complete only when every planned query ran without error AND every topic the
        # gap names got its own query (the per-gap cap can cut a third topic).
        complete = (not failed and not skipped
                    and topics_searched(plan.topics, succeeded))
        why_incomplete = (WHY_SEARCH_FAILED if failed else
                          WHY_DEADLINE if timed_out else
                          WHY_BUDGET if skipped else WHY_INCOMPLETE)
        if not succeeded:
            res = classify(plan, gap, hits=(), searched=False, inventory=docs,
                           why_not_run=why_incomplete)
        else:
            ranked = [dict(h) for h in dr.rank_hits(hits, plan.topics, plan.topics)]
            res = classify(plan, gap, hits=ranked, searched=True, inventory=docs,
                           complete=complete, why_incomplete=why_incomplete)
        res.queries_planned = len(plan.queries)
        res.queries_succeeded = len(succeeded)
        res.queries_failed = failed
        res.queries_skipped = skipped
        out.append(res)
    return out, queries_run


async def resolve_run(
    session: Any,
    run: Any,
    *,
    company_id: Any,
    inventory: Sequence[Mapping[str, Any]] | None,
    backend: Any,
    cfg: Any,
) -> dict[str, Any]:
    """The run's gap queue: read every ledger gap, resolve it, persist the resolution on
    the gap (``reconciliation_json.resolution``) and return the record the report
    carries (``v3_research.gap_resolution``)."""
    from sqlalchemy import select

    from app.models.ledger import ResearchGap

    rows = list((await session.execute(
        select(ResearchGap).where(ResearchGap.research_run_id == run.id)
        .order_by(ResearchGap.created_at)
    )).scalars().all())
    search = None
    if backend is not None and company_id is not None:
        search = _corpus_search(session, backend=backend, cfg=cfg, company_id=company_id)
    resolutions, queries_run = await resolve_gaps(
        [gap_input_of(r) for r in rows], inventory=inventory, search=search,
        deadline=time.monotonic() + MAX_QUEUE_SECONDS,
    )
    by_id = {str(r.id): r for r in rows}
    for res in resolutions:
        row = by_id.get(res.gap_id)
        if row is None:
            continue
        detail = dict(getattr(row, "reconciliation_json", None) or {})
        detail["resolution"] = res.to_dict()
        row.reconciliation_json = detail
    await session.flush()
    return {
        "version": RESOLUTION_VERSION,
        "counts": counts_of(resolutions, queries_run=queries_run),
        "gaps": [r.to_dict() for r in resolutions][:MAX_RECORDED],
        "note": (
            "Every gap was routed once: answered by a finding, re-read in the held primary "
            "documents, or recorded as not needing a re-read (with the reason). Only "
            "NOT_FOUND_AFTER_SEARCH is worded as absent from the evidence; a passage the "
            "re-read located is not a verified finding."
        ),
    }


def _corpus_search(session: Any, *, backend: Any, cfg: Any, company_id: Any) -> Any:
    """``search(query)``: one lexical, primary-documents-only corpus search for the
    subject, in a SAVEPOINT; ``None`` when it fails."""
    import uuid

    from app.services.agent_tools.corpus_search import hit_payload
    from app.services.corpus.retrieval import search_corpus
    from app.services.corpus.search.types import SearchMode

    company = company_id if isinstance(company_id, uuid.UUID) else uuid.UUID(str(company_id))

    async def search(query: str) -> list[dict[str, Any]] | None:
        try:
            async with session.begin_nested():
                results = await search_corpus(
                    session, backend=backend, cfg=cfg, query=query,
                    company_ids=[company], top_k=TOP_K, mode=SearchMode.LEXICAL,
                    exclude_injection_suspect=True, disclosures_only=True,
                )
        except Exception:  # noqa: BLE001 - a failed search is recorded, never raised
            logger.warning("gap re-read: corpus search failed")
            return None
        return [hit_payload(r) for r in results]

    return search


def resolution_of(row_or_detail: Any) -> dict[str, Any] | None:
    """The persisted resolution of a gap row (or its ``reconciliation_json``)."""
    detail = (
        row_or_detail if isinstance(row_or_detail, Mapping)
        else getattr(row_or_detail, "reconciliation_json", None)
    ) or {}
    found = detail.get("resolution") if isinstance(detail, Mapping) else None
    return dict(found) if isinstance(found, Mapping) else None


__all__ = [
    "ABSENCE_RESULTS",
    "RESULTS",
    "RESULT_WORDS",
    "GapInput",
    "GapPlan",
    "GapResolution",
    "classify",
    "counts_of",
    "gap_input_of",
    "gap_topics",
    "plan_gap",
    "render_words",
    "resolution_of",
    "resolve_gaps",
    "resolve_run",
    "reread_queries",
    "topics_searched",
    "truncated_documents",
]
