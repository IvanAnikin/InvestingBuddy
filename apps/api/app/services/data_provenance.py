"""
Phase 32A — data-provenance derivation (real / mock / mixed / unknown).

A single source of truth for turning the tri-state ``is_mock`` signal
(``bool | None`` — see ``app.schemas.agent``) into an explicit provenance label,
and back again.

Cardinal rule (Phase 32A AD-2): provenance is derived from EXPLICIT signals
only. The ABSENCE of a signal is ``"unknown"`` — it is NEVER silently coerced to
``"mock"``. Only an explicit ``is_mock is True`` (a mock provider) yields
``"mock"``; a genuinely-unknown report keeps the numbers that carry their own
real source and is flagged for human review, rather than being erased as mock.

Nothing here does I/O or raises.
"""

from __future__ import annotations

REAL = "real"
MOCK = "mock"
MIXED = "mixed"
UNKNOWN = "unknown"

_VALID_PROVENANCE = frozenset({REAL, MOCK, MIXED, UNKNOWN})


def derive_data_provenance(
    is_mock: bool | None,
    *,
    has_real_evidence: bool = False,
) -> str:
    """Return the tri-state provenance from EXPLICIT signals only.

    * explicit ``is_mock is True``                    -> ``"mock"``
    * explicit ``is_mock is False`` OR real evidence  -> ``"real"``
    * ``None`` / absent                               -> ``"unknown"`` (never mock)
    """
    if is_mock is True:
        return MOCK
    if is_mock is False or has_real_evidence:
        return REAL
    return UNKNOWN


def provenance_to_is_mock(provenance: str | None) -> bool | None:
    """Map a provenance label back onto the legacy tri-state ``is_mock`` field.

    ``mock`` -> ``True``; ``real`` / ``mixed`` -> ``False``; ``unknown`` /
    unrecognised -> ``None`` (honoured — never coerced to ``True``).
    """
    if provenance == MOCK:
        return True
    if provenance in (REAL, MIXED):
        return False
    return None


def normalise_provenance(value: str | None) -> str:
    """Coerce an arbitrary value into a valid provenance label (default unknown)."""
    return value if value in _VALID_PROVENANCE else UNKNOWN
