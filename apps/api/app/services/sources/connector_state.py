"""What each connector ACTUALLY did in this run — manual-QA reconciliation.

Every regulated-disclosure connector has two surfaces. ``fetch_filings``
returns the venue REFERENCE plus an honest "the filing content behind this
venue is not fetched" gap; ``fetch_events`` performs the LIVE retrieval. The
evidence collector calls both, in that order, and kept whatever each returned.

So a report could — and did — carry all of these at once:

    "Denmark regulated-disclosure connector scaffolded; company IR annual
     report used as primary source pending regulator integration."
    "…published via Nasdaq Nordic company disclosures … but is not fetched at
     report time; only a source reference to the venue is provided."

directly beside a list of live Nasdaq Nordic announcements retrieved in that
same run — and, for Moncler, beside eight validated financial facts extracted
from a document opened at the CONSOB-authorised storage, under the words "live
retrieval is disabled".

Each of those sentences was true when its function was written and false by the
time the report was assembled. The fix is not to soften them: it is to generate
them from the state the run actually reached.

This module is the pure record of that state. It observes; it does not fetch,
retry or re-run anything, and it never invents a retrieval that did not happen.
Reconciliation is keyed on the connector's own ``source_id`` and the gap's
typed ``gap_type`` — never on matching the prose, which would rot the moment
someone rewrote a sentence.

A metadata/reference path may legitimately still be missing after live
retrieval succeeds, so a superseded gap is REPLACED by a precise statement of
what was and was not retrieved — never silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.sources.gaps import GapSeverity, GapType, SourceGap

#: The evidence ``source_type`` that marks a live regulated-disclosure event.
EVENT_SOURCE_TYPE = "regulated_disclosure_event"

#: Gap types that make a claim about a CONNECTOR'S OWN STATE — "this venue is
#: scaffolded", "content behind it is not fetched", "live retrieval is
#: disabled". These are the only gaps live retrieval can contradict. A gap
#: about the ISSUER (no filing exists, the document was scanned) is untouched:
#: live retrieval says nothing about it.
CONNECTOR_STATE_GAP_TYPES: frozenset[GapType] = frozenset(
    {GapType.connector_scaffolded, GapType.primary_filing_unavailable}
)


@dataclass
class ConnectorRunState:
    """Which connectors reached live data in THIS run.

    Deliberately minimal: two sets of ``source_id``. Anything richer would be a
    second, drifting model of the run, and drift is the defect being fixed.
    """

    #: Connectors that returned at least one live regulated-disclosure event.
    live_event_source_ids: set[str] = field(default_factory=set)
    #: Connectors whose document was actually opened and extracted this run.
    live_document_source_ids: set[str] = field(default_factory=set)
    #: Human-readable venue name per source_id, for the replacement gap text.
    venue_names: dict[str, str] = field(default_factory=dict)

    @property
    def any_live(self) -> bool:
        return bool(self.live_event_source_ids or self.live_document_source_ids)

    def is_live(self, source_id: str | None) -> bool:
        if not source_id:
            return False
        return (
            source_id in self.live_event_source_ids
            or source_id in self.live_document_source_ids
        )

    def observe_events(self, source_id: str | None, items: "list[Any] | None") -> None:
        """Record that ``source_id`` returned live disclosure events."""
        if not source_id:
            return
        for item in items or []:
            if getattr(item, "source_type", None) == EVENT_SOURCE_TYPE:
                self.live_event_source_ids.add(source_id)
                venue = getattr(item, "content_source", None)
                if isinstance(venue, str) and venue.strip():
                    self.venue_names.setdefault(source_id, venue.strip())
                return

    def observe_document(self, source_id: str | None, *, venue: str | None) -> None:
        """Record that ``source_id``'s own document was opened and extracted."""
        if not source_id:
            return
        self.live_document_source_ids.add(source_id)
        if venue and venue.strip():
            self.venue_names[source_id] = venue.strip()

    def venue_label(self, source_id: str) -> str:
        return self.venue_names.get(source_id) or "the regulated-disclosure venue"


def _replacement_message(state: ConnectorRunState, source_id: str) -> str:
    """State what this connector DID reach, and what it still did not."""
    venue = state.venue_label(source_id)
    got_docs = source_id in state.live_document_source_ids
    got_events = source_id in state.live_event_source_ids
    if got_docs and got_events:
        reached = (
            f"Live regulated disclosures were retrieved from {venue} in this "
            "run, and the issuer's own filing held there was opened and "
            "extracted."
        )
    elif got_docs:
        reached = (
            f"The issuer's own filing held at {venue} was opened and extracted "
            "in this run."
        )
    else:
        reached = f"Live regulated disclosures were retrieved from {venue} in this run."
    return (
        f"{reached} Retrieval is bounded: only the venue's most recent "
        "announcements within the configured lookback are covered, the venue's "
        "full historical archive is not, and no filing is retrieved from any "
        "other channel. Human review required."
    )


def reconcile_connector_state_gaps(
    gaps: "list[SourceGap] | None", state: ConnectorRunState
) -> list[SourceGap]:
    """Replace connector-state gaps this run has DISPROVEN.

    A gap survives untouched unless BOTH hold: its ``source_id`` reached live
    data in this run, and its ``gap_type`` is one that makes a claim about the
    connector's own state. Everything else — issuer-side gaps, translation
    gaps, eligibility gaps, gaps from connectors that stayed reference-only —
    is passed through exactly as produced.

    At most ONE replacement gap is emitted per source_id, so a connector with
    two stale gaps does not produce two copies of the same sentence.
    """
    out: list[SourceGap] = []
    replaced: set[str] = set()
    for gap in gaps or []:
        source_id = getattr(gap, "source_id", None)
        gap_type = getattr(gap, "gap_type", None)
        if not (state.is_live(source_id) and gap_type in CONNECTOR_STATE_GAP_TYPES):
            out.append(gap)
            continue
        if source_id in replaced:
            continue
        replaced.add(str(source_id))
        out.append(
            SourceGap(
                connector_key=getattr(gap, "connector_key", str(source_id)),
                source_id=str(source_id),
                gap_type=GapType.primary_filing_unavailable,
                severity=GapSeverity.info,
                message=_replacement_message(state, str(source_id)),
                blocks_research_complete=False,
            )
        )
    return out


__all__ = [
    "CONNECTOR_STATE_GAP_TYPES",
    "EVENT_SOURCE_TYPE",
    "ConnectorRunState",
    "reconcile_connector_state_gaps",
]
