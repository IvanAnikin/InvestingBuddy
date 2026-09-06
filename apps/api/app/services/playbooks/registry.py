"""Playbook selection — V3.6 Slice 6.1.

WHAT SELECTION MUST NOT DO
==========================
**Guess.** A company whose sector the platform cannot classify gets **no** playbook and a
recorded reason, not the nearest-looking one. Applying a bank's methodology to an
industrial produces a confident analysis of the wrong things, and the wrongness is
invisible because every question got an answer.

WHAT IT MUST DO
===============
Return **every** playbook that applies. A conglomerate is legitimately both industrial and
financial, and asking only one set of questions is how a segment nobody investigated ends
up in the summary.

The union/intersection question the architecture left ambiguous is resolved in
``PlaybookSelection`` and recorded in ADR-054: **every rule of every applicable playbook
must hold.**
"""

from __future__ import annotations

from collections.abc import Sequence

from app.services.playbooks.schema import Playbook, PlaybookSelection

#: Populated by ``register``. Kept as a dict so a later version of a playbook replaces the
#: earlier one by id rather than accumulating beside it.
_REGISTRY: dict[str, Playbook] = {}


class DuplicatePlaybookError(ValueError):
    """Raised when two playbooks claim one id at the same version."""


def register(playbook: Playbook) -> Playbook:
    existing = _REGISTRY.get(playbook.playbook_id)
    if existing is not None and existing.version == playbook.version:
        raise DuplicatePlaybookError(
            f"{playbook.playbook_id} v{playbook.version} is already registered. Two "
            "different methodologies under one version make every run that cites it "
            "unreproducible."
        )
    _REGISTRY[playbook.playbook_id] = playbook
    return playbook


def all_playbooks() -> tuple[Playbook, ...]:
    return tuple(_REGISTRY[key] for key in sorted(_REGISTRY))


def get(playbook_id: str) -> Playbook | None:
    return _REGISTRY.get(playbook_id)


def select(
    *,
    sector: str | None = None,
    industry: str | None = None,
    signals: "Sequence[str]" = (),
    explicit_ids: "Sequence[str]" = (),
) -> PlaybookSelection:
    """Every applicable playbook, or none with a reason.

    ``explicit_ids`` lets an operator name a playbook directly — the escape hatch for a
    company the classifier cannot place, and the *only* way a playbook applies without a
    matching declaration. It is explicit precisely so it never happens by accident.
    """
    if explicit_ids:
        chosen = [get(pid) for pid in explicit_ids]
        missing = [pid for pid, pb in zip(explicit_ids, chosen) if pb is None]
        if missing:
            return PlaybookSelection(
                reason=f"named playbook(s) not registered: {sorted(missing)}"
            )
        return PlaybookSelection(
            playbooks=tuple(pb for pb in chosen if pb is not None),
            reason="explicitly named",
        )

    signal_set = {s.strip().casefold() for s in signals if s and s.strip()}
    matched = [
        playbook
        for playbook in all_playbooks()
        if playbook.applies_to.matches(
            sector=sector, industry=industry, signals=signal_set
        )
    ]
    if not matched:
        # No playbook, and the reason is recorded. The run then uses the Director's
        # baseline questions, which is a generic methodology honestly labelled as one
        # rather than a specialist methodology applied to the wrong company.
        return PlaybookSelection(
            reason=(
                "no playbook declares this company: "
                f"sector={sector!r} industry={industry!r} signals={sorted(signal_set)}"
            )
        )
    return PlaybookSelection(
        playbooks=tuple(matched),
        reason=f"{len(matched)} playbook(s) matched",
    )


__all__ = [
    "DuplicatePlaybookError",
    "all_playbooks",
    "get",
    "register",
    "select",
]
