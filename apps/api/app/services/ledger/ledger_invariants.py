"""Impossible combinations in one report's research state — ADR-095.

The canonical state (``canonical_state``, ADR-082/091/095) is the one ledger view: the
findings count, the gaps and their ADR-094 resolution, the conflicts between findings,
the searches, the latest period, the evidence status and the chair's verdict. This
module checks that what the report SAYS about the research cannot be true together:

* ``chair_says_no_findings_while_findings`` — the chair says there are no findings (or
  states a finding count) while the ledger holds another number;
* ``gaps_closed_exceed_gaps`` — more gaps closed (or open) than recorded;
* ``conflict_count_differs_from_ids`` — an unresolved-disagreement count that is not the
  number of conflict ids the same report lists (state, council block, chair);
* ``conflict_narrated_without_conflict`` — the chair narrates a disagreement between
  findings while the platform computed none;
* ``searches_ran_differs_from_executed`` — a search count shown that is not the search
  status block's executed count, or the block's own invariants fail;
* ``latest_period_older_than_finding`` — the latest reporting period ends before an
  ended period an issuer finding is about;
* ``evidence_adequate_with_document_not_parsed`` — the evidence status is adequate while
  a gap was searched over a document only partly parsed;
* ``chair_cites_finding_not_in_ledger`` — a chair key point cites a finding the payload's
  complete finding list does not hold.

Pure; it reads plain mappings and never raises on a partial payload. The pipeline
records the result as ``v3_research.state_invariants.impossible_combinations``; the
replay runs it over stored reports, and the tests fail on any combination it reports.
No issuer is named here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

CHAIR_NO_FINDINGS = "chair_says_no_findings_while_findings"
GAPS_CLOSED_EXCEED = "gaps_closed_exceed_gaps"
CONFLICT_COUNT_IDS = "conflict_count_differs_from_ids"
CONFLICT_UNBACKED = "conflict_narrated_without_conflict"
SEARCHES_DIFFER = "searches_ran_differs_from_executed"
LATEST_PERIOD_OLDER = "latest_period_older_than_finding"
EVIDENCE_NOT_PARSED = "evidence_adequate_with_document_not_parsed"
CHAIR_UNKNOWN_FINDING = "chair_cites_finding_not_in_ledger"

CODES: tuple[str, ...] = (
    CHAIR_NO_FINDINGS, GAPS_CLOSED_EXCEED, CONFLICT_COUNT_IDS, CONFLICT_UNBACKED,
    SEARCHES_DIFFER, LATEST_PERIOD_OLDER, EVIDENCE_NOT_PARSED, CHAIR_UNKNOWN_FINDING,
)

#: "no findings", "0 finding(s)", "zero evidence-linked findings" — but not "no findings
#: were withdrawn" or "no findings on X" (a scope).
_NO_FINDINGS_RE = re.compile(
    r"\b(?:no|zero|0)[ \t]{1,3}(?:evidence[- ]linked[ \t]{1,3})?findings?(?:\(s\))?"
    r"(?![ \t]{1,3}(?:were|was|is|are|on|about|for|of|that|which|from)\b)",
    re.IGNORECASE,
)
_DET_COUNT_RE = re.compile(r"^Deterministic synthesis:[ \t]*(\d+)[ \t]+finding\(s\)")
_UNRESOLVED_COUNT_RE = re.compile(
    r"\b(\d{1,4})[ \t]{1,3}unresolved[ \t]{1,3}(?:disagreement|conflict)", re.IGNORECASE)
_MAX_TEXT = 4000


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _g(state: Mapping[str, Any], *keys: str) -> Any:
    cur: Any = state
    for key in keys:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    return cur


def _chair_texts(v3: Mapping[str, Any]) -> Iterable[tuple[str, str]]:
    chair = _mapping(v3.get("chair"))
    synthesis = chair.get("synthesis")
    if isinstance(synthesis, str):
        yield "chair.synthesis", synthesis[:_MAX_TEXT]
    for i, point in enumerate(chair.get("key_points") or ()):
        text = _mapping(point).get("text")
        if isinstance(text, str):
            yield f"chair.key_points[{i}]", text[:_MAX_TEXT]


def _hit(out: list[dict[str, Any]], code: str, path: str, detail: str) -> None:
    out.append({"code": code, "path": path, "detail": detail[:300]})


def _findings(v3: Mapping[str, Any], out: list[dict[str, Any]],
              state: Mapping[str, Any]) -> None:
    total = _g(state, "findings", "total")
    if not isinstance(total, int):
        return
    for path, text in _chair_texts(v3):
        m = _DET_COUNT_RE.match(text.strip())
        if m and int(m.group(1)) != total:
            _hit(out, CHAIR_NO_FINDINGS, path,
                 f"states {m.group(1)} finding(s); the ledger holds {total}")
            continue
        if total > 0 and _NO_FINDINGS_RE.search(text):
            _hit(out, CHAIR_NO_FINDINGS, path,
                 f"says there are no findings; the ledger holds {total}")


def _gaps(out: list[dict[str, Any]], state: Mapping[str, Any]) -> None:
    closed, total = _g(state, "gaps", "closed"), _g(state, "gaps", "total")
    if isinstance(closed, int) and isinstance(total, int) and closed > total:
        _hit(out, GAPS_CLOSED_EXCEED, "canonical_state.gaps",
             f"{closed} closed of {total} recorded")
    listed, open_ = _g(state, "status", "gaps", "listed"), _g(state, "status", "gaps", "open")
    if isinstance(listed, int) and isinstance(open_, int) and open_ and listed > open_:
        _hit(out, GAPS_CLOSED_EXCEED, "canonical_state.status.gaps",
             f"{listed} listed of {open_} open")


def _conflicts(v3: Mapping[str, Any], out: list[dict[str, Any]],
               state: Mapping[str, Any], conflicts: Mapping[str, Any]) -> None:
    from app.services.ledger.report_invariants import (
        _LEDGER_DISAGREEMENT_RE,
        _NEGATED_DISAGREEMENT_RE,
        _narrates_conflict,
    )

    count = conflicts.get("count")
    unresolved = _g(state, "disagreements", "unresolved")
    ids = _g(state, "disagreements", "unresolved_ids")
    if isinstance(unresolved, int) and isinstance(ids, list) and unresolved != len(ids):
        _hit(out, CONFLICT_COUNT_IDS, "canonical_state.disagreements",
             f"{unresolved} unresolved with {len(ids)} id(s)")
    council = _mapping(v3.get("council"))
    said = council.get("unresolved_disagreement_count")
    if isinstance(said, int) and isinstance(unresolved, int) and said != unresolved:
        _hit(out, CONFLICT_COUNT_IDS, "council.unresolved_disagreement_count",
             f"council block says {said}; the state counts {unresolved}")
    chair = _mapping(v3.get("chair"))
    chair_ids = chair.get("unresolved_disagreement_ids")
    conflict_ids = conflicts.get("ids")
    if count == 0:
        conflict_ids = []
    # Checked when the conflicts' ledger ids are known (a run that wrote them, or none
    # computed); a replay of an older report cannot name them.
    if chair and isinstance(chair_ids, list) and isinstance(conflict_ids, list) and (
            {str(i).lower() for i in chair_ids} != {str(i).lower() for i in conflict_ids}):
        _hit(out, CONFLICT_COUNT_IDS, "chair.unresolved_disagreement_ids",
             f"chair lists {len(chair_ids)} id(s); {count} conflict(s) computed")
    for path, text in _chair_texts(v3):
        for m in _UNRESOLVED_COUNT_RE.finditer(text):
            if isinstance(count, int) and int(m.group(1)) != count:
                _hit(out, CONFLICT_COUNT_IDS, path,
                     f"states {m.group(1)} unresolved; {count} conflict(s) computed")
                break
        if count == 0:
            for sentence in re.split(r"(?<=[.!?])[ \t]+", text):
                if (_LEDGER_DISAGREEMENT_RE.search(sentence)
                        or _narrates_conflict(sentence)) and not (
                        _NEGATED_DISAGREEMENT_RE.search(sentence)):
                    if _UNRESOLVED_COUNT_RE.search(sentence):
                        continue  # a count; checked above
                    _hit(out, CONFLICT_UNBACKED, path, sentence)


def _searches(v3: Mapping[str, Any], out: list[dict[str, Any]],
              state: Mapping[str, Any], truth: Mapping[str, Any],
              repaired: set[str]) -> None:
    from app.services.ledger.search_status import check_search_status

    for item in check_search_status(truth):
        _hit(out, SEARCHES_DIFFER, f"searches.{item.get('class')}", str(item.get("code")))
    web = _mapping(truth.get("web"))
    stage = _mapping(truth.get("web_stage_total"))
    shown = _g(state, "status", "web_searches", "total")
    if web and isinstance(shown, int) and shown != web.get("executed"):
        _hit(out, SEARCHES_DIFFER, "canonical_state.status.web_searches.total",
             f"shows {shown}; {web.get('executed')} executed")
    report = _mapping(v3.get("professional_research"))
    for i, section in enumerate(report.get("sections") or ()):
        block = _mapping(_mapping(section).get("web_research"))
        said = block.get("searches_run")
        path = f"professional_research.sections[{i}].web_research.searches_run"
        if (stage and isinstance(said, int) and said != stage.get("executed")
                and path not in repaired):
            _hit(out, SEARCHES_DIFFER, path,
                 f"says {said} searches ran; {stage.get('executed')} executed")


def _latest_period(v3: Mapping[str, Any], out: list[dict[str, Any]],
                   state: Mapping[str, Any]) -> None:
    from app.services.ledger.canonical_state import (
        _FISCAL_PERIOD_BASES,
        _all_findings,
        _is_issuer,
        as_date,
        period_bounds,
    )

    latest_end = as_date(_g(state, "documents", "latest_reporting_period_end"))
    as_of = as_date(state.get("as_of")) or date.today()
    fye = _mapping(_g(state, "documents", "fiscal_year_end"))
    month = fye.get("month") if isinstance(fye.get("month"), int) else None
    non_calendar = month not in (None, 12)
    worst: tuple[date, str] | None = None
    for finding in _all_findings(v3):
        if not _is_issuer(finding):
            continue
        fiscal = finding.get("period_basis") in _FISCAL_PERIOD_BASES
        if non_calendar and not fiscal:
            continue  # an inherited key is ambiguous on a fiscal calendar (ADR-091)
        key = finding.get("period_key")
        bounds = period_bounds(key, fiscal_year_end_month=month)
        if bounds is None:
            continue
        published = as_date(finding.get("source_published_at")) or as_of
        if bounds[1] > published:
            continue  # not ended when stated (a forward date, ADR-080)
        if worst is None or bounds[1] > worst[0]:
            worst = (bounds[1], str(key))
    if worst is not None and (latest_end is None or worst[0] > latest_end):
        _hit(out, LATEST_PERIOD_OLDER, "canonical_state.documents.latest_reporting_period",
             f"latest {_g(state, 'documents', 'latest_reporting_period')} "
             f"({latest_end}) but an issuer finding is about {worst[1]} ({worst[0]})")


def _evidence(out: list[dict[str, Any]], state: Mapping[str, Any]) -> None:
    status = _g(state, "evidence", "status")
    by_result = _mapping(_g(state, "gap_resolution", "counts", "by_result"))
    not_parsed = by_result.get("DOCUMENT_NOT_PARSED")
    if status in ("adequate", "complete") and isinstance(not_parsed, int) and not_parsed:
        _hit(out, EVIDENCE_NOT_PARSED, "canonical_state.evidence.status",
             f"'{status}' with {not_parsed} DOCUMENT_NOT_PARSED gap(s)")


def _chair_citations(v3: Mapping[str, Any], out: list[dict[str, Any]],
                     state: Mapping[str, Any]) -> None:
    from app.services.ledger.report_invariants import ledger_finding_ids

    known = ledger_finding_ids(v3.get("findings"), state)
    if known is None:
        return
    for i, point in enumerate(_mapping(v3.get("chair")).get("key_points") or ()):
        fid = str(_mapping(point).get("finding_id") or "").lower()
        if fid and fid not in known:
            _hit(out, CHAIR_UNKNOWN_FINDING, f"chair.key_points[{i}]", fid)


def impossible_combinations(
    v3: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    repaired_paths: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Every impossible combination the report (``v3`` as the page shows it, after the
    invariant enforcer) holds against its canonical ``state``. ``repaired_paths``: the
    payload paths the enforcer recorded as repaired where the page reads them."""
    from app.services.ledger.canonical_state import conflict_state
    from app.services.ledger.search_status import build_search_status

    v3 = _mapping(v3)
    state = _mapping(state)
    out: list[dict[str, Any]] = []
    # The truths come from the producers themselves, never from the state under check.
    truth_searches = build_search_status(v3)
    conflicts = _mapping(state.get("conflicts")) or conflict_state(v3)
    _findings(v3, out, state)
    _gaps(out, state)
    _conflicts(v3, out, state, conflicts)
    _searches(v3, out, state, truth_searches, set(repaired_paths))
    _latest_period(v3, out, state)
    _evidence(out, state)
    _chair_citations(v3, out, state)
    return out


def summary(items: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        code = str(item.get("code"))
        counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))


__all__ = ["CODES", "impossible_combinations", "summary"]
