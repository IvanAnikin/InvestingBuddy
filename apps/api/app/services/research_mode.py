"""Research modes and their bounded technical ceilings — V3.4 Slice 4.11.

WHAT THIS CLOSES
================
[ADR-052](../../../docs/DECISIONS.md) was accepted on 2026-09-05 and never implemented.
``ResearchBudget`` still defaulted every limit to ``UNBOUNDED`` with a docstring citing
two decisions that had since been taken — so the campaign's own record said the technical
ceilings were real numbers while the code said there were none. That divergence is the
thing this module removes.

The decision, in one line:

> **Bounded technical defaults now; no business budget chosen.**

TWO INSTRUCTIONS THAT PULL APART, AND BOTH ARE HONOURED
=======================================================
*The absence of a monthly business budget is not unlimited execution*, and *V3 is not
blocked on pricing decisions*. Both hold if the **technical** ceilings are real numbers
and the **monetary** one stays unset: a run can be stopped by a round limit, a search
limit or a wall clock, and cannot be stopped by a dollar figure nobody has chosen.
``max_external_cost_usd`` is therefore the one limit that stays ``UNBOUNDED`` here.

QUICK / STANDARD / DEEP / MAX ARE DEPTH PRESETS, NOT PRICE TIERS
===============================================================
No subscription price is attached to any of them. **MAX takes the highest bounded limits
and is still finite** — an unbounded ceiling is not a generous one, it is an absent one,
and the failure it permits is a loop nobody notices until the bill arrives.

WHERE THE NUMBERS COME FROM
===========================
Measured anchors already in this repository, not invention: a full company run takes
**261-451s**, ingestion about **154s**, a council **145-190s** (live, V2). They are
estimates against those anchors and are expected to be tightened once real DeepSeek runs
are measured. **A default that is wrong but finite is recoverable; an unbounded default
is not.**

A CONFIGURED CEILING ALWAYS WINS
================================
``V3_RUN_MAX_*`` settings existed before this module and meant "the operator's hard
cap". They keep that meaning: a mode proposes, configuration disposes, and the effective
limit is the **smaller** of the two whenever the operator has set one. A mode can never
widen a ceiling somebody deliberately narrowed.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
from typing import TYPE_CHECKING

from app.services.consumption import UNBOUNDED, ResearchBudget

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings


class ResearchMode(str, Enum):
    """How deep one run goes. A depth preset, never a price tier."""

    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"
    MAX = "max"


RESEARCH_MODES: tuple[ResearchMode, ...] = tuple(ResearchMode)

DEFAULT_MODE = ResearchMode.STANDARD


@dataclass(frozen=True)
class ModeLimits:
    """Every technical ceiling one mode carries. All finite, all per run.

    The limits the *loop* enforces (rounds, tasks, tool calls) sit beside the ones the
    *budget* enforces (calls, tokens, documents, seconds) because a reader deciding
    whether a mode is safe has to see both: a run bounded to two rounds that may issue
    unlimited searches inside them is not bounded.
    """

    #: Investigation rounds. The gap-review loop terminates on this one first.
    max_rounds: int
    #: Research tasks the Director may create across all rounds.
    max_tasks: int
    #: Read-only tool calls, across every agent in the run.
    max_tool_calls: int
    max_web_searches: int
    #: Calls to an external research provider (a managed investigation), which are the
    #: most expensive single unit the platform can spend.
    max_provider_research_runs: int
    max_documents: int
    max_model_calls: int
    max_model_tokens: int
    max_wall_seconds: float

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if value is None or float(value) <= 0:
                raise ValueError(
                    f"{field.name} must be a positive finite number: an unbounded "
                    "ceiling is not a generous one, it is an absent one."
                )


#: The presets. MAX is the highest and is **still finite**.
MODE_LIMITS: dict[ResearchMode, ModeLimits] = {
    ResearchMode.QUICK: ModeLimits(
        max_rounds=1,
        max_tasks=4,
        max_tool_calls=40,
        max_web_searches=4,
        max_provider_research_runs=1,
        max_documents=5,
        max_model_calls=20,
        max_model_tokens=120_000,
        # Under the deployed gunicorn `--timeout` of 300s, so a QUICK run cannot be
        # the thing that trips the worker timeout the invariant test guards.
        max_wall_seconds=180.0,
    ),
    ResearchMode.STANDARD: ModeLimits(
        max_rounds=2,
        max_tasks=8,
        max_tool_calls=150,
        max_web_searches=12,
        max_provider_research_runs=3,
        max_documents=15,
        max_model_calls=60,
        max_model_tokens=400_000,
        # A live V2 company run measured 261-451s. STANDARD has to fit a run of that
        # shape plus a round of follow-up, with headroom rather than exactly.
        max_wall_seconds=900.0,
    ),
    ResearchMode.DEEP: ModeLimits(
        max_rounds=3,
        max_tasks=16,
        max_tool_calls=400,
        max_web_searches=30,
        max_provider_research_runs=8,
        max_documents=30,
        max_model_calls=140,
        max_model_tokens=1_200_000,
        max_wall_seconds=2_400.0,
    ),
    ResearchMode.MAX: ModeLimits(
        max_rounds=5,
        max_tasks=30,
        max_tool_calls=900,
        max_web_searches=60,
        max_provider_research_runs=20,
        max_documents=60,
        max_model_calls=300,
        max_model_tokens=3_000_000,
        max_wall_seconds=5_400.0,
    ),
}


def parse_mode(raw: str | None, *, default: ResearchMode = DEFAULT_MODE) -> ResearchMode:
    """A mode, or the default. An unrecognised name never becomes the deepest one.

    Falling back to ``default`` rather than raising is the right shape for a value that
    arrives from configuration or a request: a typo should run a bounded standard
    investigation, not fail the run and not silently run MAX.
    """
    text = (raw or "").strip().lower()
    for mode in RESEARCH_MODES:
        if mode.value == text:
            return mode
    return default


def limits_for(mode: ResearchMode) -> ModeLimits:
    return MODE_LIMITS[mode]


def _narrower(mode_value: float, configured: float) -> float:
    """The smaller of the two, treating ``UNBOUNDED`` configuration as "no opinion".

    A mode can never widen a ceiling an operator deliberately narrowed. The reverse —
    configuration widening a mode — is also refused, because the mode's number is the
    one a reader sees when they choose ``QUICK``.
    """
    if configured is None or float(configured) <= UNBOUNDED:
        return float(mode_value)
    return min(float(mode_value), float(configured))


def budget_for(
    mode: ResearchMode | str, cfg: "Settings | None" = None
) -> ResearchBudget:
    """The ``ResearchBudget`` one mode runs under, narrowed by configuration.

    ``max_external_cost_usd`` stays ``UNBOUNDED`` unless an operator sets it, and that
    is ADR-052's whole point: money remains derived from a price book nobody has filled
    in, so a monetary ceiling would be a limit expressed in a unit the platform cannot
    currently measure. The run is stopped by rounds, searches and the clock instead.
    """
    if cfg is None:
        from app.core.config import settings as default_settings

        cfg = default_settings
    resolved = mode if isinstance(mode, ResearchMode) else parse_mode(str(mode))
    limits = limits_for(resolved)
    return ResearchBudget(
        max_model_calls=int(
            _narrower(limits.max_model_calls, getattr(cfg, "v3_run_max_model_calls", 0))
        ),
        max_model_tokens=int(
            _narrower(
                limits.max_model_tokens, getattr(cfg, "v3_run_max_model_tokens", 0)
            )
        ),
        max_web_searches=int(
            _narrower(
                limits.max_web_searches, getattr(cfg, "v3_run_max_web_searches", 0)
            )
        ),
        max_documents=int(
            _narrower(limits.max_documents, getattr(cfg, "v3_run_max_documents", 0))
        ),
        max_browser_minutes=float(
            getattr(cfg, "v3_run_max_browser_minutes", 0) or UNBOUNDED
        ),
        max_wall_seconds=_narrower(
            limits.max_wall_seconds, getattr(cfg, "v3_run_max_wall_seconds", 0)
        ),
        # Deliberately NOT derived from a mode. See the docstring.
        max_external_cost_usd=float(
            getattr(cfg, "v3_run_max_external_cost_usd", 0) or UNBOUNDED
        ),
    )


__all__ = [
    "DEFAULT_MODE",
    "MODE_LIMITS",
    "RESEARCH_MODES",
    "ModeLimits",
    "ResearchMode",
    "budget_for",
    "limits_for",
    "parse_mode",
]
