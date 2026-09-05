"""Entity resolution with an explicit state — V3.2 Slice 2.3.

Answers "which legal entity is this?" with one of four states rather than with a
best guess:

    resolved      exactly one entity, on evidence strong enough to act on
    ambiguous     candidates matched and nothing separates them
    conflicting   the query's own inputs point at DIFFERENT entities
    unresolved    nothing matched

Only ``resolved`` is actionable, and the check a call site makes is membership of
``NON_ACTIONABLE_RESOLUTION_STATES`` — so "never silently merge" is a test rather
than a rule somebody has to remember.

EVIDENCE HAS STRENGTH, AND WEAK EVIDENCE IS NEVER ENOUGH
========================================================
    identifier   decisive   a LEI/CIK/ISIN is issued to one subject by a registry
    listing      strong     one ticker on one venue is one instrument at a time
    alias/name   weak       two companies may legally share a name

**A name-only match returns ``ambiguous``, even when exactly one candidate
matched.** That is the rule the whole module exists for. The platform cannot tell
"this is the one" from "a second company of this name exists and has not been
ingested yet", and treating the first reading as identity is precisely how one
issuer's numbers get attributed to another. A caller that wants the candidate can
read it off the outcome; what it cannot do is receive it labelled ``resolved``.

CONFLICTING IS STRONGER THAN AMBIGUOUS, AND THE DIFFERENCE MATTERS
==================================================================
``ambiguous`` means the platform cannot separate several plausible answers —
normal, and often resolved by more evidence. ``conflicting`` means the inputs
actively disagree: a LEI and a ticker in the *same query* resolving to two
different entities. Something upstream is wrong, more evidence will not fix it, and
a human should see it. Collapsing the two would hide the second inside the noise of
the first.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
=========================================
It does not merge, ever. It does not persist a gap — an ``entity_ambiguous`` gap
needs the Research Ledger, which is V3.5, so the state is returned and the caller
decides. It does not do fuzzy or phonetic matching: an approximate name match is a
weaker version of the evidence that is already too weak to act on. And it does not
fetch — ``IdentifierSource`` lives in ``claims`` and its live adapters are 2.3.1.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select

from app.models.legal_entity import EntityAlias, EntityIdentifier, LegalEntity, Security
from app.services.entities.identifiers import normalize_identifier
from app.services.entities.master import (
    normalize_entity_name,
    resolve_entity_by_listing,
    venue_key_for,
)
from app.services.entities.vocabulary import (
    NON_ACTIONABLE_RESOLUTION_STATES,
    RESOLUTION_AMBIGUOUS,
    RESOLUTION_CONFLICTING,
    RESOLUTION_RESOLVED,
    RESOLUTION_UNRESOLVED,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings

# ── Evidence ─────────────────────────────────────────────────────────────── #

STRENGTH_DECISIVE = "decisive"
STRENGTH_STRONG = "strong"
STRENGTH_WEAK = "weak"

#: The strengths that can carry a ``resolved``. Membership is the rule.
ACTIONABLE_STRENGTHS: frozenset[str] = frozenset({STRENGTH_DECISIVE, STRENGTH_STRONG})

EVIDENCE_IDENTIFIER = "identifier"
EVIDENCE_LISTING = "listing"
EVIDENCE_ALIAS = "alias"
EVIDENCE_NAME = "name"


@dataclass(frozen=True)
class MatchEvidence:
    """Why one candidate matched, and how much that is worth."""

    kind: str
    strength: str
    detail: str

    @property
    def is_actionable(self) -> bool:
        return self.strength in ACTIONABLE_STRENGTHS


@dataclass
class EntityCandidate:
    """One entity the query might mean, with every reason it might."""

    entity: LegalEntity
    evidence: list[MatchEvidence] = field(default_factory=list)

    @property
    def best_strength(self) -> str:
        for level in (STRENGTH_DECISIVE, STRENGTH_STRONG, STRENGTH_WEAK):
            if any(e.strength == level for e in self.evidence):
                return level
        return STRENGTH_WEAK

    @property
    def has_actionable_evidence(self) -> bool:
        return any(e.is_actionable for e in self.evidence)


@dataclass
class EntityQuery:
    """What is known about the entity being looked for.

    Every field is optional and more fields is strictly better: they are combined,
    and a disagreement between two of them is what produces ``conflicting``.
    """

    ticker: str | None = None
    exchange: str | None = None
    legal_name: str | None = None
    jurisdiction: str | None = None
    #: ``{scheme: value}`` — ``{"lei": "…"}``, ``{"cik": "320193"}``.
    identifiers: dict[str, str] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not (self.ticker or self.legal_name or self.identifiers)


@dataclass
class ResolutionOutcome:
    """A state, and everything needed to understand it."""

    state: str
    entity: LegalEntity | None = None
    candidates: list[EntityCandidate] = field(default_factory=list)
    #: Why the state is what it is, in one sentence a human can act on.
    reason: str = ""
    #: Identifiers in the query that could not be parsed at all, with the message.
    invalid_inputs: list[tuple[str, str]] = field(default_factory=list)

    @property
    def is_actionable(self) -> bool:
        """True only for ``resolved``. The check that replaces the guess."""
        return self.state not in NON_ACTIONABLE_RESOLUTION_STATES

    @property
    def candidate_ids(self) -> list[uuid.UUID]:
        return [c.entity.id for c in self.candidates]

    @property
    def dissenting_candidates(self) -> list[EntityCandidate]:
        """Candidates that matched but did not win.

        Strong evidence beating weak evidence is correct, but the weak match is not
        noise: a caller that supplied a ticker AND a name, and finds the name
        matching a *different* entity, has learned something real — either the
        document is about another company, or two entities share a name. That is
        the class of defect this repository has already seen live, where a report's
        ``legal_name`` came back as the ticker. So the losing candidates stay on the
        outcome and the reason counts them, instead of being dropped because the
        state was decided without them.
        """
        if self.entity is None:
            return list(self.candidates)
        return [c for c in self.candidates if c.entity.id != self.entity.id]


async def resolve(
    session: Any,
    query: EntityQuery,
    *,
    cfg: "Settings",
) -> ResolutionOutcome:
    """Resolve a query to a state. Never merges, never guesses, never raises.

    Returns ``unresolved`` — and issues no query — when the entity master is off.
    """
    if not getattr(cfg, "v3_entity_master_enabled", False):
        return ResolutionOutcome(
            RESOLUTION_UNRESOLVED, reason="the entity master is disabled"
        )
    if query.is_empty:
        return ResolutionOutcome(
            RESOLUTION_UNRESOLVED,
            reason="the query names no ticker, name or identifier",
        )

    by_id: dict[uuid.UUID, EntityCandidate] = {}
    invalid: list[tuple[str, str]] = []

    def note(entity: LegalEntity, evidence: MatchEvidence) -> None:
        candidate = by_id.get(entity.id)
        if candidate is None:
            by_id[entity.id] = EntityCandidate(entity=entity, evidence=[evidence])
        else:
            candidate.evidence.append(evidence)

    # ── decisive: identifiers ────────────────────────────────────────────── #
    for scheme, raw in (query.identifiers or {}).items():
        try:
            ident = normalize_identifier(scheme, raw, jurisdiction=query.jurisdiction)
        except ValueError as exc:
            # An unparseable identifier is not a match and not silently ignored:
            # it is reported, because "we could not read the LEI you gave us" is
            # a different answer from "we did not find it".
            invalid.append((scheme, str(exc)))
            continue
        entity = await _entity_holding(
            session, ident.scheme, ident.value_normalized, ident.scope_key
        )
        if entity is not None:
            note(
                entity,
                MatchEvidence(
                    EVIDENCE_IDENTIFIER,
                    STRENGTH_DECISIVE,
                    f"{ident.scheme} {ident.value_normalized}",
                ),
            )

    # ── strong: the open listing ─────────────────────────────────────────── #
    if query.ticker:
        listed = await resolve_entity_by_listing(
            session, ticker=query.ticker, exchange=query.exchange, cfg=cfg
        )
        if listed is not None:
            note(
                listed,
                MatchEvidence(
                    EVIDENCE_LISTING,
                    STRENGTH_STRONG,
                    f"{query.ticker.strip().upper()} on "
                    f"{venue_key_for(query.exchange)}",
                ),
            )

    # ── weak: names and aliases ──────────────────────────────────────────── #
    if query.legal_name:
        for entity, kind in await _name_matches(
            session, query.legal_name, jurisdiction=query.jurisdiction
        ):
            note(
                entity,
                MatchEvidence(
                    kind, STRENGTH_WEAK, f"name matches {entity.legal_name!r}"
                ),
            )

    candidates = sorted(
        by_id.values(),
        key=lambda c: (
            0
            if c.best_strength == STRENGTH_DECISIVE
            else 1
            if c.best_strength == STRENGTH_STRONG
            else 2,
            c.entity.legal_name,
        ),
    )
    return _decide(candidates, invalid_inputs=invalid)


def _decide(
    candidates: list[EntityCandidate],
    *,
    invalid_inputs: list[tuple[str, str]],
) -> ResolutionOutcome:
    """The four rules, in one place so they cannot drift between call sites."""
    if not candidates:
        reason = "no entity matched"
        if invalid_inputs:
            reason += (
                f"; {len(invalid_inputs)} supplied identifier(s) could not be read"
            )
        return ResolutionOutcome(
            RESOLUTION_UNRESOLVED, reason=reason, invalid_inputs=invalid_inputs
        )

    actionable = [c for c in candidates if c.has_actionable_evidence]

    if len(actionable) > 1:
        # The inputs disagree with each other. More evidence will not fix this;
        # something upstream is wrong and a human should see it.
        names = ", ".join(sorted(c.entity.legal_name for c in actionable))
        return ResolutionOutcome(
            RESOLUTION_CONFLICTING,
            candidates=candidates,
            reason=(
                f"{len(actionable)} entities matched on decisive or strong evidence "
                f"({names}); the query's own inputs disagree"
            ),
            invalid_inputs=invalid_inputs,
        )

    if len(actionable) == 1:
        winner = actionable[0]
        reason = "; ".join(e.detail for e in winner.evidence)
        others = [c for c in candidates if c.entity.id != winner.entity.id]
        if others:
            # Strong evidence beating weak evidence is correct, and the weak match
            # is still worth naming — see ``dissenting_candidates``.
            names = ", ".join(sorted(c.entity.legal_name for c in others))
            reason += (
                f"; {len(others)} other candidate(s) matched on weak evidence only "
                f"({names})"
            )
        return ResolutionOutcome(
            RESOLUTION_RESOLVED,
            entity=winner.entity,
            candidates=candidates,
            reason=reason,
            invalid_inputs=invalid_inputs,
        )

    # Only weak evidence. One candidate is NOT better than several here: the
    # platform cannot tell "this is the one" from "a second company of this name
    # has not been ingested yet".
    return ResolutionOutcome(
        RESOLUTION_AMBIGUOUS,
        candidates=candidates,
        reason=(
            f"{len(candidates)} candidate(s) matched on a name alone, which is "
            "never identity: two companies may legally share a name"
        ),
        invalid_inputs=invalid_inputs,
    )


async def _entity_holding(
    session: Any, scheme: str, value_normalized: str, scope_key: str
) -> LegalEntity | None:
    row = (
        await session.execute(
            select(EntityIdentifier).where(
                EntityIdentifier.scheme == scheme,
                EntityIdentifier.value_normalized == value_normalized,
                EntityIdentifier.scope_key == scope_key,
                EntityIdentifier.effective_to.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.legal_entity_id is not None:
        return (
            await session.execute(
                select(LegalEntity).where(LegalEntity.id == row.legal_entity_id)
            )
        ).scalar_one_or_none()
    return (
        await session.execute(
            select(LegalEntity)
            .join(Security, Security.legal_entity_id == LegalEntity.id)
            .where(Security.id == row.security_id)
        )
    ).scalar_one_or_none()


async def _name_matches(
    session: Any, raw_name: str, *, jurisdiction: str | None
) -> list[tuple[LegalEntity, str]]:
    """Exact matches on the folded name or on a recorded alias.

    Exact, not fuzzy. An approximate name match is a weaker version of evidence
    that is already too weak to act on, and adding a similarity threshold would
    only move the arbitrary decision into a number.
    """
    folded = normalize_entity_name(raw_name)
    if not folded:
        return []

    stmt = select(LegalEntity).where(LegalEntity.normalized_name == folded)
    if jurisdiction:
        code = jurisdiction.strip().upper()
        # A jurisdiction NARROWS but never excludes an entity whose jurisdiction is
        # unknown: NULL means "not established", and treating it as "not this one"
        # would silently drop every backfilled entity, all of which are NULL.
        stmt = stmt.where(
            or_(LegalEntity.jurisdiction == code, LegalEntity.jurisdiction.is_(None))
        )
    matches = [
        (entity, EVIDENCE_NAME)
        for entity in (await session.execute(stmt)).scalars().all()
    ]
    seen = {entity.id for entity, _ in matches}

    alias_rows = (
        (
            await session.execute(
                select(LegalEntity)
                .join(EntityAlias, EntityAlias.legal_entity_id == LegalEntity.id)
                .where(EntityAlias.normalized_alias == folded)
            )
        )
        .scalars()
        .all()
    )
    for entity in alias_rows:
        if entity.id not in seen:
            matches.append((entity, EVIDENCE_ALIAS))
            seen.add(entity.id)
    return matches


__all__ = [
    "ACTIONABLE_STRENGTHS",
    "EVIDENCE_ALIAS",
    "EVIDENCE_IDENTIFIER",
    "EVIDENCE_LISTING",
    "EVIDENCE_NAME",
    "STRENGTH_DECISIVE",
    "STRENGTH_STRONG",
    "STRENGTH_WEAK",
    "EntityCandidate",
    "EntityQuery",
    "MatchEvidence",
    "ResolutionOutcome",
    "resolve",
]
