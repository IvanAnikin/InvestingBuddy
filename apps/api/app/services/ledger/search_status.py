"""The run's search status: ONE producer — ADR-095.

WHY
===
Round 3 of the research integrity closure found the same run's searches stated by
different producers with different numbers. CFR3 ran with no search credit left: the
company web stage planned 32 queries, executed none and recorded 32 provider errors,
yet the status line said "Web searches 42" (the stage's paid network calls, failures
included, plus the investigators' tool rows) while the web panel said "0 of 32 planned
searches ran". Elsewhere "24 searches ran" (the stage's planned queries) sat beside
"Web searches 36" (the stage's network calls plus its follow-up rounds).

THE BLOCK
=========
:func:`build_search_status` reads the run's payload once and states, for every class of
search the run makes, the same six counts:

* ``planned`` — queries the plan held;
* ``attempted`` — queries handed to the search path (an outcome was recorded for each);
  ``planned - attempted`` were cut to fit the run's budget before they were issued;
* ``executed`` — a provider (or same-day cache) answer came back: a success or a
  provider error. "N searches ran" means ``executed``;
* ``succeeded`` / ``failed`` — of the executed, those that returned results and those
  the provider answered with an error. ``outcome_unknown`` counts executed searches whose
  outcome no record states (the investigators' tool rows count calls only), so
  ``succeeded + failed + outcome_unknown == executed`` always;
* ``results_returned`` — results the provider returned, when recorded.

Classes: ``web_stage`` (the company web stage), ``web_followup`` (its gap and challenge
rounds), ``investigator_web`` (the investigators' own web searches), ``corpus`` (the
investigators' queries over the run's own document index: intent, recency and held
primary-document re-reads) and ``gap_queue`` (ADR-094's re-read of each open gap). The
``web`` total sums the three web classes; ``corpus`` and ``gap_queue`` are never web
searches and never add to it.

Every renderer — the canonical status line, the chair, the professional report, the web
panels and the technical page — reads this block (``canonical_state.searches``).

Pure; no issuer is named here; nothing here calls a network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

SEARCH_STATUS_VERSION = 1

WEB_CLASSES: tuple[str, ...] = ("web_stage", "web_followup", "investigator_web")
CLASSES: tuple[str, ...] = (*WEB_CLASSES, "corpus", "gap_queue")

_COUNT_KEYS: tuple[str, ...] = (
    "planned", "attempted", "executed", "succeeded", "failed", "outcome_unknown",
    "not_issued", "from_cache",
)

#: The invariant codes :func:`check_search_status` reports.
SEARCH_EXECUTED_EXCEEDS_ATTEMPTED = "search_executed_exceeds_attempted"
SEARCH_ATTEMPTED_EXCEEDS_PLANNED = "search_attempted_exceeds_planned"
SEARCH_OUTCOMES_DO_NOT_SUM = "search_outcomes_do_not_sum_to_executed"
SEARCH_SUCCEEDED_WITHOUT_CREDIT = "search_succeeded_with_zero_credits"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return 0


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _counts(*, planned: int, succeeded: int, failed: int, not_issued: int = 0,
            outcome_unknown: int = 0, from_cache: int = 0,
            results_returned: int | None = None) -> dict[str, Any]:
    """One class's counts, made consistent by construction: an executed search is a
    success, a failure or an unknown outcome; an attempted one was executed or not
    issued; a planned one was attempted or cut."""
    executed = succeeded + failed + outcome_unknown
    attempted = executed + not_issued
    planned = max(planned, attempted)
    return {
        "planned": planned,
        "attempted": attempted,
        "executed": executed,
        "succeeded": succeeded,
        "failed": failed,
        "outcome_unknown": outcome_unknown,
        "not_issued": not_issued,
        "from_cache": min(from_cache, succeeded),
        "not_attempted": planned - attempted,
        "results_returned": results_returned,
    }


def _web_stage(context: Mapping[str, Any]) -> dict[str, Any] | None:
    queries = _mapping(context.get("queries"))
    if not queries:
        return None
    results = _mapping(context.get("results"))
    seen = results.get("seen")
    return _counts(
        planned=_int(queries.get("planned")),
        # The stage counts a cache serve as executed: an answer came back (spec §18).
        succeeded=_int(queries.get("executed")),
        failed=_int(queries.get("failed")),
        not_issued=_int(queries.get("not_issued")),
        from_cache=_int(queries.get("from_cache")),
        results_returned=_int(seen) if isinstance(seen, int) else None,
    )


def _web_followup(context: Mapping[str, Any]) -> dict[str, Any] | None:
    followup = _mapping(context.get("followup"))
    rounds = [r for r in followup.get("rounds") or () if isinstance(r, Mapping)]
    if not followup or not rounds:
        return None
    planned = succeeded = failed = not_issued = candidates = 0
    for item in rounds:
        issued = item.get("queries")
        planned += len(issued) if isinstance(issued, list) else 0
        succeeded += _int(item.get("executed"))
        failed += _int(item.get("failed"))
        not_issued += _int(item.get("not_issued"))
        candidates += _int(item.get("candidates"))
    return _counts(planned=planned, succeeded=succeeded, failed=failed,
                   not_issued=not_issued, results_returned=candidates)


def _investigator_web(consumption: Mapping[str, Any]) -> dict[str, Any] | None:
    if "web_search_calls" not in consumption:
        return None
    calls = _int(consumption.get("web_search_calls"))
    # The tool rows count calls, not outcomes: each is an executed search whose result no
    # record states. Never guessed as a success.
    return _counts(planned=calls, succeeded=0, failed=0, outcome_unknown=calls)


def domain_cost(
    questions: Sequence[tuple[str | None, Sequence[Mapping[str, Any]]]]
) -> dict[str, dict[str, int]]:
    """What each domain's acquisition spent, from the questions' own acquisition logs.

    ADR-095 — the one place the per-domain search counts are computed; the professional
    report's "Research effort by domain" and the ``corpus`` class below read it."""
    out: dict[str, dict[str, int]] = {}
    for domain, log in questions:
        bucket = out.setdefault(
            domain or "unclassified",
            {"tool_calls": 0, "corpus_queries": 0, "external_searches": 0, "fetches": 0},
        )
        for step in log or []:
            if not isinstance(step, Mapping):
                continue
            rung = step.get("rung")
            if rung == "platform_tools":
                bucket["tool_calls"] += _int(step.get("tool_calls"))
            elif rung == "corpus_by_intent":
                queries = step.get("queries")
                bucket["corpus_queries"] += len(queries) if isinstance(queries, list) else 0
            elif rung in ("recency_corpus", "primary_document_reread"):
                # ADR-076 newest-documents pass; ADR-084 held-primary-document re-read.
                bucket["corpus_queries"] += _int(step.get("tool_calls"))
            elif rung == "external_search" and step.get("query"):
                bucket["external_searches"] += 1
                bucket["fetches"] += _int(step.get("fetched"))
    return dict(sorted(out.items()))


def _question_logs(v3: Mapping[str, Any]
                   ) -> list[tuple[str | None, Sequence[Mapping[str, Any]]]]:
    out: list[tuple[str | None, Sequence[Mapping[str, Any]]]] = []
    for question in v3.get("question_graph") or ():
        question = _mapping(question)
        log = question.get("acquisition_log")
        if isinstance(log, list):
            domain = question.get("domain")
            out.append((str(domain) if domain else None, log))
    return out


def _corpus(v3: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    logs = _question_logs(v3)
    if not logs:
        return None, {}
    by_domain = domain_cost(logs)
    queries = sum(d["corpus_queries"] for d in by_domain.values())
    # A query over the run's own index: no provider, so every logged query came back.
    return _counts(planned=queries, succeeded=queries, failed=0), by_domain


def _gap_queue(v3: Mapping[str, Any]) -> dict[str, Any] | None:
    record = _mapping(v3.get("gap_resolution"))
    counts = _mapping(record.get("counts"))
    if not counts:
        return None
    if "queries_planned" in counts:
        skipped = _int(counts.get("queries_skipped"))
        return _counts(
            planned=_int(counts.get("queries_planned")),
            succeeded=_int(counts.get("queries_succeeded")),
            failed=_int(counts.get("queries_failed")),
            not_issued=skipped,
        )
    run = _int(counts.get("reread_queries"))
    return _counts(planned=run, succeeded=0, failed=0, outcome_unknown=run)


def _credits(v3: Mapping[str, Any]) -> dict[str, Any]:
    """The web stage's search credits as its cost record states them."""
    context = _mapping(v3.get("web_context"))
    consumption = _mapping(v3.get("consumption"))
    stage_units = _mapping(consumption.get("web_stage")) or _mapping(context.get("cost_units"))
    unreported = stage_units.get("unreported") or ()
    credits = _num(stage_units.get("tavily_credits"))
    return {
        "provider": context.get("provider") if isinstance(context.get("provider"), str)
        else None,
        "tavily_credits": credits,
        "tavily_credits_reported": credits is not None and "tavily_credits" not in unreported,
    }


def build_search_status(v3: Mapping[str, Any]) -> dict[str, Any]:
    """ADR-095 — the run's search status, from the payload. Never raises on a partial
    payload; a class the run has no record of is ``None``."""
    v3 = _mapping(v3)
    context = _mapping(v3.get("web_context"))
    consumption = _mapping(v3.get("consumption"))
    corpus, by_domain = _corpus(v3)
    classes: dict[str, dict[str, Any] | None] = {
        "web_stage": _web_stage(context),
        "web_followup": _web_followup(context),
        "investigator_web": _investigator_web(consumption),
        "corpus": corpus,
        "gap_queue": _gap_queue(v3),
    }
    web: dict[str, Any] = {key: 0 for key in _COUNT_KEYS}
    web["results_returned"] = 0
    web_known = False
    for name in WEB_CLASSES:
        block = classes[name]
        if block is None:
            continue
        web_known = True
        for key in _COUNT_KEYS:
            web[key] += int(block[key])
        if isinstance(block.get("results_returned"), int):
            web["results_returned"] += block["results_returned"]
    web["not_attempted"] = web["planned"] - web["attempted"]
    status: dict[str, Any] = {
        "version": SEARCH_STATUS_VERSION,
        "web": web if web_known else None,
        "classes": classes,
        "by_domain": by_domain,
        "credits": _credits(v3),
        # The company web stage and its follow-up rounds: one provider, one budget.
        "web_stage_total": _sum_classes(classes, ("web_stage", "web_followup")),
    }
    status["violations"] = check_search_status(status)
    return status


def _sum_classes(classes: Mapping[str, Any], names: Sequence[str]) -> dict[str, int] | None:
    blocks: list[Mapping[str, Any]] = []
    for name in names:
        block = classes.get(name)
        if isinstance(block, Mapping):
            blocks.append(block)
    if not blocks:
        return None
    totals: dict[str, int] = {}
    for key in _COUNT_KEYS:
        totals[key] = sum((int(b[key]) for b in blocks), 0)
    return totals


def check_search_status(status: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The block's own invariants: ``executed <= attempted <= planned``,
    ``succeeded + failed + outcome_unknown == executed`` for every class, and a web stage
    whose provider reported zero credits had no successful network search (a cache
    serve costs nothing). Empty when the block is consistent."""
    out: list[dict[str, Any]] = []
    for name, block in _mapping(status.get("classes")).items():
        if not isinstance(block, Mapping):
            continue
        if int(block["executed"]) > int(block["attempted"]):
            out.append({"code": SEARCH_EXECUTED_EXCEEDS_ATTEMPTED, "class": name})
        if int(block["attempted"]) > int(block["planned"]):
            out.append({"code": SEARCH_ATTEMPTED_EXCEEDS_PLANNED, "class": name})
        if (int(block["succeeded"]) + int(block["failed"]) + int(block["outcome_unknown"])
                != int(block["executed"])):
            out.append({"code": SEARCH_OUTCOMES_DO_NOT_SUM, "class": name})
    credits = _mapping(status.get("credits"))
    stage = _mapping(status.get("web_stage_total"))
    if (credits.get("provider") == "tavily" and credits.get("tavily_credits_reported")
            and credits.get("tavily_credits") == 0.0 and stage
            and int(stage["succeeded"]) - int(stage["from_cache"]) > 0):
        out.append({"code": SEARCH_SUCCEEDED_WITHOUT_CREDIT, "class": "web_stage"})
    return out


def web_status_line(status: Mapping[str, Any]) -> dict[str, Any] | None:
    """The ``canonical_state.status.web_searches`` line (ADR-091 keys kept): the web
    searches EXECUTED, the company web stage's (with its follow-up rounds) and the
    investigators', and how many of them succeeded and failed."""
    web = _mapping(status.get("web"))
    if not web:
        return None
    stage = _mapping(status.get("web_stage_total"))
    investigators = _mapping(_mapping(status.get("classes")).get("investigator_web"))
    return {
        "total": int(web["executed"]),
        "web_stage": int(stage["executed"]) if stage else 0,
        "investigators": int(investigators["executed"]) if investigators else 0,
        "planned": int(web["planned"]),
        "succeeded": int(web["succeeded"]),
        "failed": int(web["failed"]),
        "outcome_unknown": int(web["outcome_unknown"]),
    }


__all__ = [
    "CLASSES",
    "SEARCH_STATUS_VERSION",
    "WEB_CLASSES",
    "build_search_status",
    "check_search_status",
    "domain_cost",
    "web_status_line",
]
