"""Reading the council bucket that nothing has ever read. V3.17.3.

``discovery_runs.config_json["discovery_council"]["review"]["candidates_to_research_next"]``
has been written for several phases and consumed by nobody. This module is the consumer.

WHY BUCKET MEMBERSHIP *IS* THE COERCION
=======================================
``discovery_council._aggregate_chair`` places a note by
``_ACTION_TO_FIELD.get(note.internal_action)`` — an action outside the allowlist maps to
no field and the note lands in **no bucket at all**. So a candidate's presence in
``candidates_to_research_next`` is itself the evidence that its action was recognised and
coerced upstream.

That is why this module reads the bucket rather than the raw ``internal_action``: the raw
value is the model's word, and the bucket is the platform's reading of it. The predicate
is still handed a named action and still checks it, because a reader of
``predicates.evaluate`` should not have to know this to see that the check happens.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.research_decision import DECISION_RESEARCH_NEXT
from app.services.escalation.controller import CandidateFacts

#: Where the envelope lives, mirroring ``market_discovery_service.COUNCIL_STORAGE_KEY``.
COUNCIL_STORAGE_KEY = "discovery_council"
RESEARCH_NEXT_BUCKET = "candidates_to_research_next"


def _as_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def candidates_proposed_for_research(
    session: AsyncSession, run: Any
) -> list[CandidateFacts]:
    """The run's ``research_next`` candidates, as facts the controller can evaluate.

    Returns an empty list — never raises — when the council never ran, the envelope is
    absent, or the bucket is empty. "No council review" and "the council proposed nothing"
    both correctly produce no work, and neither is an error.
    """
    config = getattr(run, "config_json", None) or {}
    envelope = config.get(COUNCIL_STORAGE_KEY) or {}
    review = envelope.get("review") or {}
    entries = review.get(RESEARCH_NEXT_BUCKET) or []
    if not isinstance(entries, list) or not entries:
        return []

    candidate_ids = [
        cid
        for cid in (
            _as_uuid(e.get("candidate_id")) for e in entries if isinstance(e, dict)
        )
        if cid
    ]
    if not candidate_ids:
        return []

    from app.models.discovery import DiscoveryCandidate

    rows = (
        await session.execute(
            select(DiscoveryCandidate).where(DiscoveryCandidate.id.in_(candidate_ids))
        )
    ).scalars().all()
    by_id = {row.id: row for row in rows}
    companies = await _resolve_companies(session, rows)

    facts: list[CandidateFacts] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        candidate_id = _as_uuid(entry.get("candidate_id"))
        row = by_id.get(candidate_id) if candidate_id else None
        if row is None:
            # The bucket names a candidate that no longer exists. Skipped rather than
            # guessed at: escalating "some candidate that was here once" is worse than
            # escalating nothing.
            continue
        facts.append(
            CandidateFacts(
                candidate_id=candidate_id,
                company_id=companies.get((row.ticker, row.exchange)),
                # Bucket membership is the coerced action — see the module docstring.
                coerced_action=DECISION_RESEARCH_NEXT,
                blocking_gap_count=getattr(row, "blocking_gap_count", None),
                confidence=_confidence(entry),
            )
        )
    return facts


async def _resolve_companies(
    session: AsyncSession, rows: "Sequence[Any]"
) -> dict[tuple[str, str], uuid.UUID]:
    """Map (ticker, exchange) to a Company id for the candidates that have one.

    **A ``DiscoveryCandidate`` has no ``company_id``.** It carries a ticker and an
    exchange; a ``Company`` row exists only once somebody has promoted the candidate. So
    escalation can only research a candidate that has been promoted, and a candidate that
    has not is refused by the controller with ``candidate_has_no_company`` rather than
    silently skipped.

    THE EXCHANGE IS PART OF THE KEY, NOT DECORATION
    -----------------------------------------------
    The same ticker exists on several exchanges, and matching on ticker alone would
    cheerfully queue paid research against the wrong issuer — a mistake that is invisible
    afterwards, because the resulting report would look entirely plausible.
    """
    pairs = {(r.ticker, r.exchange) for r in rows if r.ticker and r.exchange}
    if not pairs:
        return {}

    from app.models.company import Company

    tickers = {t for t, _ in pairs}
    found = (
        await session.execute(select(Company).where(Company.ticker.in_(tickers)))
    ).scalars().all()
    return {
        (c.ticker, c.exchange): c.id for c in found if (c.ticker, c.exchange) in pairs
    }


def _confidence(entry: dict[str, Any]) -> float | None:
    """The chair's confidence, or ``None`` when it did not give one.

    ``None`` is preserved rather than defaulted: an absent confidence is not a low one,
    and defaulting it to 0.0 would make every unscored candidate look uncertain enough to
    justify paid research.
    """
    raw = entry.get("confidence")
    if raw is None:
        return None
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return None


__all__ = ["RESEARCH_NEXT_BUCKET", "candidates_proposed_for_research"]
