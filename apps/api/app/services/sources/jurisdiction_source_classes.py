"""Jurisdiction-aware naming of the REGULATED-DISCLOSURE source class.

WHY THIS MODULE EXISTS
======================
Catalyst discovery attempts the SEC recent-filings provider for every issuer.
For a US issuer that is right. For a Danish, Italian or Swiss issuer it
produces, in the human-facing "News & Catalyst Discovery" section:

    source_classes_attempted: [..., "sec_filings"]
    missing_sources:          ["sec_recent_filings"]
    warnings:                 ["SEC CIK not available for PNDORA. Company may
                               not be SEC-registered (U.S. only). ..."]

which tells a reviewer that this issuer's regulated-disclosure channel is
missing. It is not missing — SEC EDGAR is simply not this issuer's channel, and
the report's own "Regulated Disclosures" section is at that moment listing five
Nasdaq Nordic announcements. Naming the wrong venue as the gap is the same
class of defect the regulator-channel labels fixed elsewhere: a report
describing a European issuer in US vocabulary.

WHAT THIS DOES
==============
Reclassifies — never deletes. For an issuer whose venue SEC EDGAR does not
cover, the SEC source classes move out of "attempted"/"missing" into an
explicit ``not_applicable_sources`` entry that carries the provider's own
message, and the issuer's real regulated-disclosure channel is reported in
their place, named after the venue that actually serves it. Its state is
sourced from what was actually retrieved, so it is "successful" only when
disclosures were.

An SEC-eligible issuer is untouched: a genuine SEC attempt, and a genuine SEC
gap, must stay visible exactly as it is today. An issuer with no resolvable
exchange is treated as SEC-eligible (the legacy ticker-only default) rather
than having its channel guessed.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.exchange_registry import is_sec_eligible
from app.services.sources.company_evidence import regulator_venue_display_name

#: The catalyst source-class ids that name SEC EDGAR specifically.
#: ``sec_filings`` is the ATTEMPT label, ``sec_recent_filings`` the GAP label —
#: two different vocabularies for one channel, both emitted by
#: ``catalyst_discovery_service``.
SEC_SOURCE_CLASSES: frozenset[str] = frozenset({"sec_filings", "sec_recent_filings"})

#: The jurisdiction-neutral id for "the issuer's regulated-disclosure channel".
#: Deliberately generic: a venue with no display name still gets a correctly
#: NAMED class, just without the venue label.
REGULATED_DISCLOSURE_CLASS = "regulated_disclosures"

#: Fragments identifying a warning that is ABOUT the SEC channel. Matched as a
#: substring on the provider's own text; anything else is left alone.
_SEC_WARNING_MARKERS: tuple[str, ...] = (
    "SEC CIK not available",
    "SEC EDGAR submissions returned",
    "SEC recent filings provider error",
)

_NOT_APPLICABLE = "not_applicable_jurisdiction"


@dataclass(frozen=True)
class SourceClassView:
    """The human-facing source-class inventory for one issuer."""

    sec_eligible: bool
    regulated_disclosure_venue: str | None
    source_classes_attempted: list[str]
    source_classes_successful: list[str]
    missing_sources: list[str]
    not_applicable_sources: list[dict[str, object]]
    warnings: list[str]
    #: True when SEC was reclassified out of the gap list for this issuer.
    reclassified: bool


def _venue_label(venue: str | None) -> str:
    return venue or "the issuer's own regulated-disclosure venue"


def classify_source_classes(
    *,
    exchange: str | None,
    country: str | None,
    attempted: list[str] | None,
    successful: list[str] | None,
    missing: list[str] | None,
    warnings: list[str] | None,
    regulated_disclosure_count: int = 0,
) -> SourceClassView:
    """Name the regulated-disclosure source class after the issuer's own venue.

    Pure and deterministic — no I/O, no issuer-specific branch. ``exchange`` and
    ``country`` come from the report's own company identity.
    """
    attempted_in = list(attempted or [])
    successful_in = list(successful or [])
    missing_in = list(missing or [])
    warnings_in = list(warnings or [])

    sec_eligible = is_sec_eligible(exchange)
    venue = regulator_venue_display_name(exchange, country)

    if sec_eligible:
        # A genuine SEC attempt for an SEC-eligible issuer is never hidden,
        # and a genuine SEC gap for one is never renamed.
        return SourceClassView(
            sec_eligible=True,
            regulated_disclosure_venue=venue,
            source_classes_attempted=attempted_in,
            source_classes_successful=successful_in,
            missing_sources=missing_in,
            not_applicable_sources=[],
            warnings=warnings_in,
            reclassified=False,
        )

    sec_attempted = [c for c in attempted_in if c in SEC_SOURCE_CLASSES]
    sec_missing = [c for c in missing_in if c in SEC_SOURCE_CLASSES]
    if not sec_attempted and not sec_missing:
        # Nothing to reclassify — the SEC channel was never named here.
        return SourceClassView(
            sec_eligible=False,
            regulated_disclosure_venue=venue,
            source_classes_attempted=attempted_in,
            source_classes_successful=successful_in,
            missing_sources=missing_in,
            not_applicable_sources=[],
            warnings=warnings_in,
            reclassified=False,
        )

    kept_warnings = [
        w
        for w in warnings_in
        if not any(marker in str(w) for marker in _SEC_WARNING_MARKERS)
    ]
    moved_warnings = [
        str(w)
        for w in warnings_in
        if any(marker in str(w) for marker in _SEC_WARNING_MARKERS)
    ]

    detail = (
        "SEC EDGAR covers issuers registered with the U.S. Securities and "
        f"Exchange Commission. This issuer's venue ({exchange or 'unknown'}) is "
        f"not one of them; its regulated disclosures are published via "
        f"{_venue_label(venue)} and are reported under the "
        "'regulated_disclosures' source class and the Regulated Disclosures "
        "section. This is NOT a gap in the issuer's regulated-disclosure "
        "coverage."
    )
    not_applicable: list[dict[str, object]] = [
        {
            "source_class": name,
            "reason": _NOT_APPLICABLE,
            "attempted": name in sec_attempted,
            "detail": detail,
            # Nothing the provider said is discarded — it is re-filed under the
            # channel it is actually about.
            "provider_messages": moved_warnings if name in sec_attempted else [],
        }
        for name in sorted(set(sec_attempted) | set(sec_missing))
    ]

    out_attempted = sorted(
        {c for c in attempted_in if c not in SEC_SOURCE_CLASSES}
        | {REGULATED_DISCLOSURE_CLASS}
    )
    out_missing = [c for c in missing_in if c not in SEC_SOURCE_CLASSES]
    out_successful = list(successful_in)
    if regulated_disclosure_count > 0:
        if REGULATED_DISCLOSURE_CLASS not in out_successful:
            out_successful.append(REGULATED_DISCLOSURE_CLASS)
        out_successful = sorted(set(out_successful))
    elif REGULATED_DISCLOSURE_CLASS not in out_missing:
        # Honest: the channel IS this issuer's, and nothing came back from it.
        out_missing = sorted({*out_missing, REGULATED_DISCLOSURE_CLASS})

    return SourceClassView(
        sec_eligible=False,
        regulated_disclosure_venue=venue,
        source_classes_attempted=out_attempted,
        source_classes_successful=out_successful,
        missing_sources=out_missing,
        not_applicable_sources=not_applicable,
        warnings=kept_warnings,
        reclassified=True,
    )


# ---------------------------------------------------------------------------
# The APPLICABLE regulated venue, for any surface that must not treat "no SEC"
# as a gap
# ---------------------------------------------------------------------------
#
# ``classify_source_classes`` above fixes the REPORT's source-class inventory.
# The discovery council had the same defect one layer earlier: its evidence
# pack handed every candidate a bare ``sec_eligible: false`` and a run-level
# known gap reading "N candidate(s) are not SEC-eligible", so a council looking
# at Richemont, Pandora, Kering, Moncler and Swatch concluded that none of them
# had regulated filings and prioritised on price momentum instead.
#
# SEC EDGAR is one venue among several. For an issuer it does not cover, its
# absence is not a research gap — the question is whether the issuer's OWN
# venue produced anything. This helper answers that in one place, generically,
# from the same exchange/country registry every other layer uses. There is no
# issuer name anywhere in it.


@dataclass(frozen=True)
class ApplicableVenue:
    """Which regulated-disclosure venue actually applies to one issuer."""

    #: True when SEC EDGAR is this issuer's applicable venue.
    sec_applicable: bool
    #: Display name of the applicable venue, or None when none resolved.
    venue_name: str | None
    #: The venue as the council should NAME it in prose.
    venue_label: str
    #: True when the platform could not resolve any venue for this issuer.
    venue_unresolved: bool

    def gap_statement(self, *, disclosures_retrieved: int) -> str | None:
        """The honest regulated-disclosure gap for this issuer, or None.

        A gap exists when the APPLICABLE venue returned nothing — never
        because a venue that does not cover this issuer returned nothing.
        """
        if disclosures_retrieved > 0:
            return None
        if self.venue_unresolved:
            return (
                "No regulated-disclosure venue is resolved for this issuer, so "
                "no regulated filing could be retrieved for it."
            )
        return (
            f"No supported regulated filing was retrieved from the applicable "
            f"venue ({self.venue_label}) for this issuer."
        )


def applicable_regulated_venue(
    *, exchange: str | None, country: str | None = None
) -> ApplicableVenue:
    """Resolve the regulated-disclosure venue that applies to one issuer.

    Pure and deterministic. An issuer with NO resolvable exchange keeps the
    legacy ticker-only default (SEC-applicable) rather than having its venue
    guessed — the same fail-closed rule ``classify_source_classes`` uses.
    """
    if is_sec_eligible(exchange):
        return ApplicableVenue(
            sec_applicable=True,
            venue_name="SEC EDGAR",
            venue_label="SEC EDGAR",
            venue_unresolved=False,
        )
    venue = regulator_venue_display_name(exchange, country)
    return ApplicableVenue(
        sec_applicable=False,
        venue_name=venue,
        venue_label=venue or "the issuer's own regulated-disclosure venue",
        venue_unresolved=venue is None,
    )
