"""Canonical entity/segment SCOPE semantics for one extracted financial fact.

Private-use production readiness, Phase PR-A.

Before this module, "scope" was a free-text ``str | None`` that three different
layers each interpreted with their own private rule:

  * ``primary_document_extractor._infer_scope`` produced it from a heading;
  * ``primary_fact_parser._infer_prose_scope`` produced it from a sentence;
  * ``final_report_generator._high_confidence_facts_for`` decided whether it
    meant "Group" by lower-casing it and testing membership of a local label
    set (``_GROUP_SCOPE_LABELS``).

That worked only while the string stayed in memory. It could not be persisted
without also persisting the interpretation rule, and it made "is this the
consolidated figure?" a string-matching question at every read site.

This module makes scope a small, typed, DECIDABLE value:

  ``FactScope(scope_type, scope_name, scope_key)``

``scope_type`` is the coarse semantic every consumer branches on. ``scope_name``
keeps the as-found label for humans. ``scope_key`` is the stable identity that
dedupe / supersession / series-grouping compare on.

Fail-closed by construction: a label the vocabulary does not recognise as
Group is NOT assumed to be Group, and a label that carries no scope signal at
all stays ``UNKNOWN`` rather than being coerced either way. Unknown is a real,
representable answer here — it is what the pipeline has always meant by
"the excerpt's heading gives no scope signal", and the report layer's existing
implicit-Group convention for an unscoped FRESH fact is preserved deliberately
and explicitly rather than by accident.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── Scope types ──────────────────────────────────────────────────────────── #

SCOPE_TYPE_GROUP = "group"
SCOPE_TYPE_SEGMENT = "segment"
#: Persisted as SQL NULL. Never coerced to ``group`` at write time.
SCOPE_TYPE_UNKNOWN: str | None = None

VALID_SCOPE_TYPES: frozenset[str] = frozenset({SCOPE_TYPE_GROUP, SCOPE_TYPE_SEGMENT})

#: Labels that mean "this fact IS the consolidated/Group figure". Kept here so
#: exactly one table decides it, rather than one copy per consuming module.
#: Historically this lived in ``final_report_generator._GROUP_SCOPE_LABELS``.
GROUP_SCOPE_LABELS: frozenset[str] = frozenset(
    {
        "group",
        "the group",
        "consolidated",
        "consolidated group",
        "group total",
        "total group",
        "groupe",  # French issuers (URD) use the local form in headings
        "konzern",
        "gruppo",
    }
)

#: Column widths from migration 018 — clipped here so a pathological heading can
#: never overflow the column and abort a whole persistence transaction.
_SCOPE_NAME_MAX = 200
_SCOPE_KEY_MAX = 220

_WS_RE = re.compile(r"\s+")


def _normalize_label(raw: str | None) -> str | None:
    """Collapse whitespace and strip decoration; ``None`` for an empty label."""
    if not raw:
        return None
    text = _WS_RE.sub(" ", str(raw)).strip().strip("–—-:•·|").strip()
    if not text:
        return None
    return text[:_SCOPE_NAME_MAX]


def is_group_label(raw: str | None) -> bool:
    """True when ``raw`` names the consolidated Group, per the one vocabulary."""
    label = _normalize_label(raw)
    if label is None:
        return False
    return label.casefold() in GROUP_SCOPE_LABELS


@dataclass(frozen=True)
class FactScope:
    """The persisted, typed scope of one extracted fact.

    ``scope_type is None`` means UNKNOWN — the document gave no scope signal.
    It is deliberately distinct from ``group``: a Group slot may be filled from
    an unknown-scope fact only where the pipeline's pre-existing implicit
    convention already allowed it, and never as a result of a DB round-trip
    silently erasing a segment attribution.
    """

    scope_type: str | None = None
    scope_name: str | None = None

    @property
    def scope_key(self) -> str | None:
        """Stable identity for dedupe / supersession / series grouping.

        ``'group'`` | ``'segment:<casefolded name>'`` | ``None``. Casefolded so
        ``"Specialist Watchmakers"`` and ``"SPECIALIST WATCHMAKERS"`` are one
        series; the distinct NAME is still preserved on ``scope_name``.
        """
        if self.scope_type == SCOPE_TYPE_GROUP:
            return SCOPE_TYPE_GROUP
        if self.scope_type == SCOPE_TYPE_SEGMENT and self.scope_name:
            return f"segment:{self.scope_name.casefold()}"[:_SCOPE_KEY_MAX]
        return None

    @property
    def is_group(self) -> bool:
        return self.scope_type == SCOPE_TYPE_GROUP

    @property
    def is_segment(self) -> bool:
        return self.scope_type == SCOPE_TYPE_SEGMENT

    @property
    def is_unknown(self) -> bool:
        return self.scope_type is None

    @property
    def label(self) -> str | None:
        """The legacy free-text form, for the in-memory ``scope`` field.

        Round-trips: ``parse_scope(scope.label) == scope`` for every scope this
        module can build, so the legacy string API stays exactly as expressive
        as it was while the typed columns carry the decidable form.
        """
        if self.scope_type == SCOPE_TYPE_GROUP:
            return SCOPE_TYPE_GROUP
        if self.scope_type == SCOPE_TYPE_SEGMENT:
            return self.scope_name
        return None

    def human_label(self) -> str:
        """Short display label. Never emits ``None`` into human-facing text."""
        if self.scope_type == SCOPE_TYPE_GROUP:
            return "Group"
        if self.scope_type == SCOPE_TYPE_SEGMENT and self.scope_name:
            return self.scope_name
        return "Scope not stated"


#: The single UNKNOWN instance, so callers can compare identity cheaply.
UNKNOWN_SCOPE = FactScope()
GROUP_SCOPE = FactScope(scope_type=SCOPE_TYPE_GROUP)


def parse_scope(raw: str | None) -> FactScope:
    """Interpret the legacy free-text ``scope`` string into a typed ``FactScope``.

    This is the ONE place the free-text → typed decision is made. A blank or
    absent label is UNKNOWN; a label in the Group vocabulary is ``group``;
    anything else non-empty is a named ``segment`` (fail-closed — an
    unrecognised business-area heading is a segment, never silently the Group).
    """
    label = _normalize_label(raw)
    if label is None:
        return UNKNOWN_SCOPE
    if label.casefold() in GROUP_SCOPE_LABELS:
        return GROUP_SCOPE
    return FactScope(scope_type=SCOPE_TYPE_SEGMENT, scope_name=label)


def scope_from_columns(
    scope_type: str | None,
    scope_name: str | None,
    scope_key: str | None = None,
) -> FactScope:
    """Rebuild a ``FactScope`` from persisted columns.

    Defensive about rows written by an older or a partially-migrated writer:
    an unrecognised ``scope_type`` degrades to UNKNOWN rather than being
    trusted, and a ``segment`` row that somehow lost its name degrades to
    UNKNOWN too (an anonymous segment is not a usable identity, and it must
    certainly not become the Group). ``scope_key`` is accepted for forward
    compatibility and re-derived rather than trusted, so a stale key can never
    silently re-point a fact at a different series.
    """
    if scope_type == SCOPE_TYPE_GROUP:
        return GROUP_SCOPE
    if scope_type == SCOPE_TYPE_SEGMENT:
        name = _normalize_label(scope_name)
        if name is None:
            return UNKNOWN_SCOPE
        return FactScope(scope_type=SCOPE_TYPE_SEGMENT, scope_name=name)
    # Legacy row (pre-018) that only ever had the free-text label available, or
    # an unknown/NULL type: fall back to interpreting whatever name is present.
    return parse_scope(scope_name)


def scope_columns(scope: FactScope | str | None) -> dict[str, str | None]:
    """The three persisted column values for ``scope``.

    Accepts either a typed ``FactScope`` or the legacy free-text label, so every
    writer can be migrated independently without a flag day.
    """
    resolved = scope if isinstance(scope, FactScope) else parse_scope(scope)
    return {
        "scope_type": resolved.scope_type,
        "scope_name": resolved.scope_name,
        "scope_key": resolved.scope_key,
    }


def same_scope(a: FactScope | str | None, b: FactScope | str | None) -> bool:
    """True when two scopes are the SAME series.

    Two UNKNOWN scopes are NOT declared the same series: comparability is
    fail-closed, and "we do not know what either of these describes" is not
    evidence that they describe the same thing. This is what stops a historical
    series from silently mixing a Group row with a segment row whose heading
    was never captured.
    """
    ka = (a if isinstance(a, FactScope) else parse_scope(a)).scope_key
    kb = (b if isinstance(b, FactScope) else parse_scope(b)).scope_key
    if ka is None or kb is None:
        return False
    return ka == kb


__all__ = [
    "GROUP_SCOPE",
    "GROUP_SCOPE_LABELS",
    "SCOPE_TYPE_GROUP",
    "SCOPE_TYPE_SEGMENT",
    "SCOPE_TYPE_UNKNOWN",
    "UNKNOWN_SCOPE",
    "VALID_SCOPE_TYPES",
    "FactScope",
    "is_group_label",
    "parse_scope",
    "same_scope",
    "scope_columns",
    "scope_from_columns",
]
