"""Provider benchmark harness — V3.4 Slice 4.5.

WHAT IS BEING MEASURED, AND WHY IT IS NOT PRICE
===============================================
The primary metric is **`cost_per_verified_finding`**, never price alone. A provider that
is half the price and produces a third as many claims that survive InvestingBuddy's own
verification is more expensive per unit of research, and a comparison on price would
choose it. The denominator is the thing this platform actually wants and the thing only
this platform can measure.

The denominator is real, not notional: it comes from slice 4.4's gate, which fetches each
cited source through the platform's own guarded fetcher and refuses a claim it cannot
find. **A provider cannot score well by being confident.**

THE THREE WAYS A BENCHMARK LIES, AND WHAT STOPS EACH
====================================================
1. **Reporting an unpriced provider as free.** `CostEstimate.amount_usd = None` means
   *unpriced*, and an unpriced provider's `cost_per_verified_finding` is `None`. It is
   never `0.0`, it never sorts first, and `cheapest()` refuses to rank a field it cannot
   compare. A price book nobody has filled in produces "unknown", which is the truth.
2. **Estimating a provider it cannot run.** Exa, Perplexity, Gemini and Anthropic have no
   credentials **by decision** (ADR-048/050). The honest output is a row saying
   *unavailable, and why* — not an extrapolation from a published price list. There is no
   code path in this module that produces a number for a provider that did not run.
3. **Reporting a rate over nothing.** A provider whose every lead was unverifiable has a
   survival rate of `0.0`; a provider that produced no leads at all has a survival rate of
   `None`. Collapsing those makes a vendor that never answered look like one that answered
   badly, and the second is a much better vendor than the first.

DETERMINISM
===========
The harness is deterministic given its inputs: it does not sample, does not average across
hidden retries, and records the exact task list and provider configuration in the result,
so a run six months later can be compared against this one rather than against a memory
of it. `raw_provider_metadata` is retained for the same reason — provider defaults change
and nobody remembers what they were.

NOT IN CI
=========
A real benchmark spends money at a vendor. This module is importable and unit-testable
with fakes; running it against live providers is `scripts/v3-provider-benchmark.py`,
which is manual, opt-in and budget-capped.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from app.services.consumption import ConsumptionUnits, PriceBook, derive_cost
from app.services.providers.contracts import (
    INCOMPLETE_STATUSES,
    LEAD_REJECTED,
    LEAD_UNVERIFIABLE,
    LEAD_VERIFIED,
    ResearchLead,
    ResearchProviderResult,
)

#: Why a provider produced no result. A closed vocabulary for the same reason every other
#: refusal vocabulary in this codebase is closed: "unavailable" aggregated by reason is a
#: finding, and aggregated by free text is a list of sentences.
UNAVAILABLE_NO_CREDENTIAL = "no_credential"
UNAVAILABLE_NOT_APPROVED = "not_approved"
UNAVAILABLE_NOT_CONFIGURED = "not_configured"
UNAVAILABLE_ERROR = "error"
UNAVAILABLE_TIMEOUT = "timeout"

UNAVAILABLE_REASONS: frozenset[str] = frozenset(
    {
        UNAVAILABLE_NO_CREDENTIAL,
        UNAVAILABLE_NOT_APPROVED,
        UNAVAILABLE_NOT_CONFIGURED,
        UNAVAILABLE_ERROR,
        UNAVAILABLE_TIMEOUT,
    }
)


@dataclass(frozen=True)
class BenchmarkTask:
    """One question, and what a good answer to it looks like.

    ``expected_source_hosts`` is not a scoring rule about *correctness* — the verification
    gate decides that. It records whether the provider went to the issuer's own filings or
    to a content farm, which is a **quality** finding invisible from the answer.
    """

    task_id: str
    question: str
    #: The issuer the question is about, so a result can be attributed and so the
    #: verification gate has an entity to scope against.
    company_ticker: str | None = None
    context: str | None = None
    expected_source_hosts: tuple[str, ...] = ()
    notes: str | None = None


class LeadVerifier(Protocol):
    """How the harness turns leads into verified findings.

    Injected rather than imported so the benchmark can run against a real gate, a
    recorded fixture, or a stub that refuses everything — and so this module never
    performs a fetch itself.
    """

    async def __call__(self, lead: ResearchLead) -> str:
        """Return one of ``LEAD_STATUSES``."""
        ...  # pragma: no cover - protocol


@dataclass
class TaskOutcome:
    """What one provider did with one task."""

    task_id: str
    provider: str
    status: str
    lead_count: int = 0
    verifiable_lead_count: int = 0
    verified_count: int = 0
    rejected_count: int = 0
    unverifiable_count: int = 0
    undecided_count: int = 0
    #: Cited hosts the task named as authoritative. A provider that answers well from a
    #: content farm is answering a different question from the one that was asked.
    expected_host_hits: int = 0
    distinct_cited_hosts: tuple[str, ...] = ()
    consumption: ConsumptionUnits = field(default_factory=ConsumptionUnits)
    elapsed_seconds: float = 0.0
    warnings: tuple[str, ...] = ()
    unavailable_reason: str | None = None

    @property
    def ran(self) -> bool:
        return self.unavailable_reason is None


@dataclass
class ProviderScore:
    """One provider's line in the report. Every ratio can be ``None``.

    ``None`` means *not measurable*, and the distinction is the point of the class:
    a rate over nothing is not zero, and an unpriced cost is not free.
    """

    provider: str
    tasks_attempted: int = 0
    tasks_run: int = 0
    lead_count: int = 0
    verifiable_lead_count: int = 0
    verified_count: int = 0
    rejected_count: int = 0
    unverifiable_count: int = 0
    undecided_count: int = 0
    expected_host_hits: int = 0
    consumption: ConsumptionUnits = field(default_factory=ConsumptionUnits)
    elapsed_seconds: float = 0.0
    estimated_cost_usd: float | None = None
    unpriced_units: tuple[str, ...] = ()
    unavailable_reasons: tuple[str, ...] = ()

    @property
    def decided_count(self) -> int:
        return self.verified_count + self.rejected_count + self.unverifiable_count

    @property
    def verification_survival_rate(self) -> float | None:
        """Verified over decided. ``None`` when nothing was decided.

        A provider that produced no leads has an **unknown** survival rate. Reporting
        ``0.0`` would rank it below one that tried and was wrong, and one that tried is
        the better vendor of the two.
        """
        return self.verified_count / self.decided_count if self.decided_count else None

    @property
    def unverifiable_rate(self) -> float | None:
        """How much of the output cites nothing. The single most damning provider
        statistic, and invisible from the prose."""
        return self.unverifiable_count / self.lead_count if self.lead_count else None

    @property
    def cost_per_verified_finding(self) -> float | None:
        """**The primary metric.** ``None`` when unpriced or when nothing was verified.

        Two different ``None``s, deliberately not distinguished by the value: a reader is
        told which it is by ``unpriced_units`` and ``verified_count``. What must never
        happen is a number here that came from a cost of unknown.
        """
        if self.estimated_cost_usd is None:
            return None
        if not self.verified_count:
            return None
        return self.estimated_cost_usd / self.verified_count

    @property
    def is_priced(self) -> bool:
        return self.estimated_cost_usd is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "tasks_attempted": self.tasks_attempted,
            "tasks_run": self.tasks_run,
            "lead_count": self.lead_count,
            "verifiable_lead_count": self.verifiable_lead_count,
            "verified_count": self.verified_count,
            "rejected_count": self.rejected_count,
            "unverifiable_count": self.unverifiable_count,
            "undecided_count": self.undecided_count,
            "expected_host_hits": self.expected_host_hits,
            "verification_survival_rate": self.verification_survival_rate,
            "unverifiable_rate": self.unverifiable_rate,
            "estimated_cost_usd": self.estimated_cost_usd,
            "is_priced": self.is_priced,
            "unpriced_units": list(self.unpriced_units),
            "cost_per_verified_finding": self.cost_per_verified_finding,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "unavailable_reasons": list(self.unavailable_reasons),
            "consumption": self.consumption.to_dict(),
        }


@dataclass
class BenchmarkReport:
    """One benchmark run, reproducible from what it records."""

    run_id: uuid.UUID = field(default_factory=uuid.uuid4)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    task_ids: tuple[str, ...] = ()
    scores: list[ProviderScore] = field(default_factory=list)
    outcomes: list[TaskOutcome] = field(default_factory=list)
    #: Providers that were asked for and could not run, with the reason. Present in the
    #: report rather than absent from it: an omitted provider reads as one nobody
    #: considered, and "we were not permitted to run it" is a result.
    unavailable: dict[str, str] = field(default_factory=dict)
    price_book_is_empty: bool = True

    def score_for(self, provider: str) -> ProviderScore | None:
        for score in self.scores:
            if score.provider == provider:
                return score
        return None

    def cheapest(self) -> ProviderScore | None:
        """The best `cost_per_verified_finding` among providers that HAVE one.

        Returns ``None`` when no provider is both priced and productive. That is the
        current expected answer, and returning a winner anyway would be the benchmark
        lying in the most useful-looking way available to it.
        """
        ranked = [
            score
            for score in self.scores
            if score.cost_per_verified_finding is not None
        ]
        if not ranked:
            return None
        return min(
            ranked, key=lambda s: (s.cost_per_verified_finding, s.provider)  # type: ignore[arg-type,return-value]
        )

    def to_dict(self) -> dict[str, Any]:
        winner = self.cheapest()
        return {
            "run_id": str(self.run_id),
            "started_at": self.started_at.isoformat(),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "task_ids": list(self.task_ids),
            "price_book_is_empty": self.price_book_is_empty,
            "scores": [s.to_dict() for s in self.scores],
            "unavailable": dict(self.unavailable),
            "cheapest_by_cost_per_verified_finding": (
                winner.provider if winner is not None else None
            ),
        }


def _host_of(url: str | None) -> str | None:
    from urllib.parse import urlsplit

    try:
        return (urlsplit(url or "").hostname or "").lower() or None
    except (ValueError, TypeError):
        return None


async def run_task(
    provider: Any,
    task: BenchmarkTask,
    *,
    verifier: LeadVerifier,
    max_seconds: int = 300,
) -> TaskOutcome:
    """Run one task against one provider and score what came back.

    A provider that raises produces an ``unavailable`` outcome with the reason, never an
    exception that ends the benchmark: the point of a comparison is that one vendor
    failing does not delete the others' results.
    """
    started = time.monotonic()
    try:
        result: ResearchProviderResult = await provider.investigate(
            question=task.question, context=task.context, max_seconds=max_seconds
        )
    except Exception as exc:  # noqa: BLE001 - a vendor failure is a datum
        return TaskOutcome(
            task_id=task.task_id,
            provider=getattr(provider, "provider_id", "unknown"),
            status="failed",
            elapsed_seconds=time.monotonic() - started,
            unavailable_reason=UNAVAILABLE_ERROR,
            warnings=(f"{type(exc).__name__}",),
        )

    outcome = TaskOutcome(
        task_id=task.task_id,
        provider=result.provider,
        status=result.status,
        lead_count=len(result.research_leads),
        verifiable_lead_count=sum(
            1 for lead in result.research_leads if lead.is_verifiable
        ),
        consumption=result.consumption,
        warnings=tuple(result.warnings),
    )
    for lead in result.research_leads:
        status = await verifier(lead)
        if status == LEAD_VERIFIED:
            outcome.verified_count += 1
        elif status == LEAD_REJECTED:
            outcome.rejected_count += 1
        elif status == LEAD_UNVERIFIABLE:
            outcome.unverifiable_count += 1
        else:
            # `pending`/`verifying`: the gate could not decide, which is a statement
            # about the platform's reader and NOT about the provider. Counted apart so
            # it never lands in a survival rate that would blame the vendor for it.
            outcome.undecided_count += 1

    hosts = {
        host
        for host in (
            _host_of(lead.claimed_source_url) for lead in result.research_leads
        )
        if host
    }
    outcome.distinct_cited_hosts = tuple(sorted(hosts))
    if task.expected_source_hosts:
        wanted = {h.lower() for h in task.expected_source_hosts}
        outcome.expected_host_hits = sum(
            1 for host in hosts if any(host == w or host.endswith("." + w) for w in wanted)
        )
    outcome.elapsed_seconds = time.monotonic() - started
    if result.status in INCOMPLETE_STATUSES and not result.research_leads:
        outcome.unavailable_reason = (
            UNAVAILABLE_TIMEOUT if result.status == "timeout" else UNAVAILABLE_ERROR
        )
    return outcome


async def run_benchmark(
    providers: "dict[str, Any]",
    tasks: "Sequence[BenchmarkTask]",
    *,
    verifier: LeadVerifier,
    prices: PriceBook | None = None,
    unavailable: "dict[str, str] | None" = None,
    max_seconds: int = 300,
) -> BenchmarkReport:
    """Score every provider over every task.

    ``unavailable`` is the providers the caller *knows* it cannot run, with the reason —
    Exa, Perplexity, Gemini and Anthropic have no credentials by decision. They appear in
    the report as unavailable rather than being silently absent, because an omitted
    provider reads as one nobody considered.
    """
    prices = prices or PriceBook()
    report = BenchmarkReport(
        task_ids=tuple(task.task_id for task in tasks),
        price_book_is_empty=prices.is_empty,
        unavailable=dict(unavailable or {}),
    )
    for name, reason in report.unavailable.items():
        if reason not in UNAVAILABLE_REASONS:
            raise ValueError(
                f"{reason!r} is not a recognised unavailability reason for {name!r}. "
                "'unavailable' aggregated by reason is a finding; aggregated by free "
                f"text it is a list of sentences. Recognised: "
                f"{', '.join(sorted(UNAVAILABLE_REASONS))}."
            )

    for name, provider in providers.items():
        score = ProviderScore(provider=name, tasks_attempted=len(tasks))
        totals = ConsumptionUnits()
        reasons: list[str] = []
        for task in tasks:
            outcome = await run_task(
                provider, task, verifier=verifier, max_seconds=max_seconds
            )
            report.outcomes.append(outcome)
            if not outcome.ran:
                reasons.append(outcome.unavailable_reason or UNAVAILABLE_ERROR)
                continue
            score.tasks_run += 1
            score.lead_count += outcome.lead_count
            score.verifiable_lead_count += outcome.verifiable_lead_count
            score.verified_count += outcome.verified_count
            score.rejected_count += outcome.rejected_count
            score.unverifiable_count += outcome.unverifiable_count
            score.undecided_count += outcome.undecided_count
            score.expected_host_hits += outcome.expected_host_hits
            score.elapsed_seconds += outcome.elapsed_seconds
            totals = totals + outcome.consumption
        score.consumption = totals
        cost = derive_cost(totals, prices)
        score.estimated_cost_usd = cost.estimated_usd
        score.unpriced_units = cost.unpriced_units
        score.unavailable_reasons = tuple(sorted(set(reasons)))
        report.scores.append(score)

    report.completed_at = datetime.now(timezone.utc)
    return report


def render_report(report: BenchmarkReport) -> str:
    """A human-readable table. ``unknown`` is printed as ``unknown``, never as ``0``."""

    def fmt(value: float | None, digits: int = 4) -> str:
        return "unknown" if value is None else f"{value:.{digits}f}"

    lines = [
        f"Benchmark {report.run_id}",
        f"tasks: {len(report.task_ids)}   price book: "
        f"{'EMPTY (every cost is unknown)' if report.price_book_is_empty else 'configured'}",
        "",
        f"{'provider':<20}{'run':>5}{'leads':>7}{'verif':>7}{'rej':>6}{'unver':>7}"
        f"{'undec':>7}{'survival':>10}{'cost/verified':>16}",
        "-" * 85,
    ]
    for score in sorted(report.scores, key=lambda s: s.provider):
        lines.append(
            f"{score.provider:<20}{score.tasks_run:>5}{score.lead_count:>7}"
            f"{score.verified_count:>7}{score.rejected_count:>6}"
            f"{score.unverifiable_count:>7}{score.undecided_count:>7}"
            f"{fmt(score.verification_survival_rate, 3):>10}"
            f"{fmt(score.cost_per_verified_finding):>16}"
        )
    if report.unavailable:
        lines.append("")
        lines.append("not run:")
        for name, reason in sorted(report.unavailable.items()):
            lines.append(f"  {name:<18} {reason}")
    winner = report.cheapest()
    lines.append("")
    lines.append(
        f"cheapest by cost_per_verified_finding: {winner.provider}"
        if winner
        else "cheapest by cost_per_verified_finding: NOT DETERMINED — no provider is "
        "both priced and productive. An unpriced provider is never the cheapest one."
    )
    return "\n".join(lines)


__all__ = [
    "UNAVAILABLE_ERROR",
    "UNAVAILABLE_NOT_APPROVED",
    "UNAVAILABLE_NOT_CONFIGURED",
    "UNAVAILABLE_NO_CREDENTIAL",
    "UNAVAILABLE_REASONS",
    "UNAVAILABLE_TIMEOUT",
    "BenchmarkReport",
    "BenchmarkTask",
    "LeadVerifier",
    "ProviderScore",
    "TaskOutcome",
    "render_report",
    "run_benchmark",
    "run_task",
]
