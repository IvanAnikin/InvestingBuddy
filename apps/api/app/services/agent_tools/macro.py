"""The macro series tool — V3.4 Slice 4.7.

``get_macro_series`` was one of the twelve names V3.3 reserved and could not implement:
the tool vocabulary named it, and nothing had a time series to return. The observation
store gives it one.

WHAT THIS TOOL REFUSES
======================
**It never fetches.** The tool reads what the platform has already stored. A read-only
tool that could trigger a network call would be an agent-controlled outbound request, and
the closed tool list exists precisely so an injected instruction cannot cause one.

**A macro period is never compared with a fiscal one.** The result labels its frequency
and its period keys are visibly statistical (``2025-Q2``, ``2025-07``). Richemont's
FY2025 ends in March, and a calendar 2025 macro reading placed beside it as "the same
year" is a comparison nobody made deliberately.

**Unknown units are labelled unknown.** The World Bank publishes no unit field; the
platform reads it from the indicator name and stores ``unknown`` when the name does not
state one. A tool that dropped that would hand an agent a bare number.

**A revision is visible.** ``as_of`` returns what the platform knew at a date, so a
research run reproducing a past report gets that report's macro context rather than
today's — and ``revisions`` shows the change itself, which is often the finding.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.services.agent_tools.contracts import (
    TOOL_GET_MACRO_SERIES,
    ToolCost,
    ToolSpec,
)
from app.services.macro import store as macro_store

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.agent_tools.registry import ToolRegistry
    from app.services.agent_tools.session import ToolContext

DEFAULT_LIMIT = 40
MAX_LIMIT = 200


def validate_get_macro_series(arguments: dict[str, Any]) -> dict[str, Any]:
    dataset_key = str(arguments.get("dataset_key") or "").strip()
    series_key = str(arguments.get("series_key") or "").strip()
    if not dataset_key or not series_key:
        raise ValueError(
            "get_macro_series needs both dataset_key and series_key: two publishers may "
            "legitimately use the same indicator code and they are not the same series."
        )
    limit = int(arguments.get("limit") or DEFAULT_LIMIT)
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    as_of_raw = arguments.get("as_of")
    as_of: datetime | None = None
    if as_of_raw:
        if isinstance(as_of_raw, datetime):
            as_of = as_of_raw
        else:
            try:
                as_of = datetime.fromisoformat(str(as_of_raw))
            except ValueError as exc:
                raise ValueError(
                    "as_of must be an ISO-8601 timestamp. A date this tool could not "
                    "read would silently become 'now', which returns today's revision "
                    "of a series a past report cited."
                ) from exc
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
    period_keys = tuple(
        str(key).strip() for key in (arguments.get("period_keys") or []) if str(key).strip()
    )
    return {
        "dataset_key": dataset_key,
        "series_key": series_key,
        "limit": limit,
        "as_of": as_of,
        "period_keys": period_keys,
        "include_revisions": bool(arguments.get("include_revisions")),
    }


async def _get_macro_series(
    context: "ToolContext", arguments: dict[str, Any]
) -> dict[str, Any]:
    series = await macro_store.find_series(
        context.session,
        dataset_key=arguments["dataset_key"],
        series_key=arguments["series_key"],
    )
    if series is None:
        # No `series` key at all when there is nothing to describe — the same shape
        # `lookup_entity` uses. A payload carrying an empty series object invites a
        # caller to read its fields.
        return {
            "found": False,
            "reason": "series_not_held",
            "summary": (
                f"The platform holds no series {arguments['series_key']!r} in dataset "
                f"{arguments['dataset_key']!r}. That is a gap in what has been "
                "ingested, not a statement that the publisher has no such series."
            ),
            "contains_untrusted_content": False,
        }

    as_of = arguments["as_of"]
    if as_of is not None:
        observations = await macro_store.as_of(
            context.session, series, as_of, limit=arguments["limit"]
        )
    else:
        observations = await macro_store.latest(
            context.session,
            series,
            period_keys=arguments["period_keys"],
            limit=arguments["limit"],
        )
    if as_of is not None and arguments["period_keys"]:
        wanted = set(arguments["period_keys"])
        observations = [o for o in observations if o.period_key in wanted]

    payload: dict[str, Any] = {
        "found": True,
        "series": {
            "dataset_key": arguments["dataset_key"],
            "series_key": series.series_key,
            "display_name": series.display_name,
            "indicator_code": series.indicator_code,
            "unit": series.unit,
            "currency": series.currency,
            "scale": series.scale,
            "geography": series.geography,
            "frequency": series.frequency,
            "seasonal_adjustment": series.seasonal_adjustment,
        },
        "as_of": as_of.isoformat() if as_of else None,
        "observations": [o.to_dict() for o in observations],
        "summary": (
            f"{len(observations)} {series.frequency} observation(s) of "
            f"{series.display_name!r} in {series.unit}"
            + (f", as known at {as_of.isoformat()}" if as_of else "")
        ),
        "contains_untrusted_content": False,
    }
    if arguments["include_revisions"] and observations:
        payload["revisions"] = {
            observations[0].period_key: [
                value.to_dict()
                for value in await macro_store.revision_history(
                    context.session, series, period_key=observations[0].period_key
                )
            ]
        }
    return payload


GET_MACRO_SERIES_SPEC = ToolSpec(
    name=TOOL_GET_MACRO_SERIES,
    description=(
        "Official statistical observations the platform already holds, for one series. "
        "Returns each period's current reading, or — with `as_of` — the reading that was "
        "current at that date, because a macro series is revised and a past report cited "
        "the value of its own day. Never fetches; never returns a value without its unit "
        "and frequency."
    ),
    handler=_get_macro_series,
    validate_arguments=validate_get_macro_series,
    cost=ToolCost(),
    instrumented_units=(),
)


def register_macro_tools(registry: "ToolRegistry") -> "ToolRegistry":
    registry.register(GET_MACRO_SERIES_SPEC)
    return registry


__all__ = [
    "DEFAULT_LIMIT",
    "GET_MACRO_SERIES_SPEC",
    "MAX_LIMIT",
    "register_macro_tools",
    "validate_get_macro_series",
]
