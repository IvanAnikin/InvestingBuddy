"""Model slots and the router — V3.4 Slice 4.1.

DOMAIN LOGIC NAMES A SLOT, NEVER A MODEL
========================================
That is the whole rule. ``gpt-5.6-sol`` will not be the strongest synthesis model for
long, and a codebase that has its name in a council prompt has hard-coded a vendor's
release schedule into its business logic. So the Chair asks for ``chair_model`` and the
router answers, or does not.

AN UNRESOLVABLE SLOT DEGRADES; IT DOES NOT CRASH
================================================
``get_llm_client`` already returns ``None`` rather than raising when a provider is
unavailable, and the council already has a deterministic fallback path. The router keeps
that property: ``resolve`` returns ``None`` and ``why_unresolved`` says which of the
three reasons applied — unconfigured, unregistered, or refused by governance. "The chair
model is not configured" and "the chair provider may not see this content" are different
problems with different owners, and a single ``None`` would hide both.

MODEL NAMES ARE CONFIGURATION AND STILL UNDECIDED
=================================================
Every slot defaults to **empty**. `OPEN DECISION #5 <../../../../docs/v3/OPEN_DECISIONS.md>`_
(which model fills which slot) is open, and its recommendation is explicit that absolute
cost is small — about $0.19 per report for a cheap-analyst/strong-Chair split — so the
routing decision is about **rate-limit headroom**, not the bill. Guessing a default here
would answer a question asked of somebody else, and an unconfigured slot degrading to the
deterministic path is a working system rather than a broken one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.services.providers.governance import ProviderGovernance, default_governance

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings

# ── The slots ────────────────────────────────────────────────────────────── #

SLOT_CLASSIFICATION = "classification_model"
SLOT_CHEAP_RESEARCH = "cheap_research_model"
SLOT_DOCUMENT_REASONING = "document_reasoning_model"
SLOT_RESEARCH_DIRECTOR = "research_director_model"
SLOT_RED_TEAM = "red_team_model"
SLOT_CHAIR = "chair_model"
SLOT_DEEP_RESEARCH = "deep_research_provider"
SLOT_TRANSLATION = "translation_model"

MODEL_SLOTS: frozenset[str] = frozenset(
    {
        SLOT_CLASSIFICATION,
        SLOT_CHEAP_RESEARCH,
        SLOT_DOCUMENT_REASONING,
        SLOT_RESEARCH_DIRECTOR,
        SLOT_RED_TEAM,
        SLOT_CHAIR,
        SLOT_DEEP_RESEARCH,
        SLOT_TRANSLATION,
    }
)

#: The slot whose whole value is that a DIFFERENT vendor fills it. A model challenging
#: its own family's output shares its blind spots, and vendor diversity is most of what a
#: Red Team is for (`#7 <../../../../docs/v3/OPEN_DECISIONS.md>`_).
DIVERSITY_PREFERRED_SLOTS: frozenset[str] = frozenset({SLOT_RED_TEAM})

#: Slots whose absence must not stop a run. Every one, currently: the platform worked
#: without any of them before V3.4 and has to keep working while they are unconfigured.
DEGRADABLE_SLOTS: frozenset[str] = MODEL_SLOTS

#: Passed as ``access_class`` when a resolution carries **no content at all** — a
#: capability report, or a router health check. It has to be typed out, because the
#: alternative is a default of ``None`` that silently skips the governance check, and a
#: fail-open in the module whose whole purpose is the check is the worst place for one.
NO_CONTENT = "__no_content__"

UNRESOLVED_NOT_CONFIGURED = "slot_not_configured"
UNRESOLVED_PROVIDER_NOT_REGISTERED = "provider_not_registered"
UNRESOLVED_GOVERNANCE_REFUSED = "governance_refused"


def require_slot(value: str | None) -> str:
    slot = (value or "").strip()
    if slot not in MODEL_SLOTS:
        raise ValueError(
            f"{value!r} is not a model slot. Domain logic names a slot, never a model. "
            f"Slots: {', '.join(sorted(MODEL_SLOTS))}."
        )
    return slot


@dataclass
class SlotResolution:
    """What a slot resolved to, or why it did not.

    ``provider`` is ``None`` for an unresolved slot, and ``reason`` says which of the
    three causes applied. Collapsing them into one ``None`` would hide "nobody configured
    this" behind "governance refused this", which have different owners.
    """

    slot: str
    provider: Any | None = None
    provider_id: str | None = None
    model: str | None = None
    reason: str | None = None
    detail: str = ""

    @property
    def resolved(self) -> bool:
        return self.provider is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "provider_id": self.provider_id,
            "model": self.model,
            "resolved": self.resolved,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass
class ProviderRegistry:
    """Registered provider instances, by id. Holds no opinion about slots."""

    providers: dict[str, Any] = field(default_factory=dict)

    def register(self, provider: Any) -> Any:
        provider_id = str(getattr(provider, "provider_id", "") or "").strip()
        if not provider_id:
            raise ValueError(
                "a provider must declare a provider_id: it is the key governance, "
                "consumption and the benchmark all attribute against."
            )
        self.providers[provider_id] = provider
        return provider

    def get(self, provider_id: str | None) -> Any | None:
        return self.providers.get((provider_id or "").strip())

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.providers))

    def __len__(self) -> int:
        return len(self.providers)


@dataclass
class ModelRouter:
    """Resolves a slot to a provider, or explains why it could not."""

    registry: ProviderRegistry
    #: ``{slot: provider_id}``. Every slot absent by default — see the module docstring.
    slot_assignments: dict[str, str] = field(default_factory=dict)
    governance: ProviderGovernance = field(default_factory=default_governance)

    def assign(self, slot: str, provider_id: str) -> None:
        self.slot_assignments[require_slot(slot)] = provider_id

    def resolve(self, slot: str, *, access_class: str) -> SlotResolution:
        """Resolve a slot for content of ``access_class``.

        ``access_class`` is **required**, and part of resolution rather than checked
        afterwards. A router that hands back a provider and leaves the governance check
        to the caller has made the check optional, and an optional governance check is
        one somebody forgets at the one call site that mattered. Pass
        :data:`NO_CONTENT` — spelled out — when the resolution genuinely carries no
        content, such as a capability report.
        """
        name = require_slot(slot)
        provider_id = (self.slot_assignments.get(name) or "").strip()
        if not provider_id:
            return SlotResolution(
                slot=name,
                reason=UNRESOLVED_NOT_CONFIGURED,
                detail=(
                    f"no provider is assigned to {name}. Which model fills it is OPEN "
                    "DECISION #5 and unconfigured is the honest default; the caller "
                    "degrades to the deterministic path."
                ),
            )

        provider = self.registry.get(provider_id)
        if provider is None:
            return SlotResolution(
                slot=name,
                provider_id=provider_id,
                reason=UNRESOLVED_PROVIDER_NOT_REGISTERED,
                detail=(
                    f"{name} is assigned to {provider_id!r}, which is not registered — "
                    "usually a missing credential, which is a configuration problem "
                    "rather than a governance one"
                ),
            )

        if access_class != NO_CONTENT:
            refusal = self.governance.refuse_reason(provider_id, access_class)
            if refusal is not None:
                return SlotResolution(
                    slot=name,
                    provider_id=provider_id,
                    reason=UNRESOLVED_GOVERNANCE_REFUSED,
                    detail=refusal,
                )

        return SlotResolution(
            slot=name,
            provider=provider,
            provider_id=provider_id,
            model=str(getattr(provider, "model", "") or "") or None,
        )

    def report(self) -> dict[str, Any]:
        """Every slot's state, for a run record.

        A run that produced a thin analysis because four slots were unconfigured should
        say so, and this is what it says it with.
        """
        # NO_CONTENT: a report resolves slots to describe them, not to send anything.
        resolutions = [
            self.resolve(slot, access_class=NO_CONTENT) for slot in sorted(MODEL_SLOTS)
        ]
        return {
            "registered_providers": list(self.registry.ids()),
            "slots": [r.to_dict() for r in resolutions],
            "resolved_slots": [r.slot for r in resolutions if r.resolved],
            "unresolved_slots": {
                r.slot: r.reason for r in resolutions if not r.resolved
            },
            "diversity_preferred_slots": sorted(DIVERSITY_PREFERRED_SLOTS),
        }

    def shares_vendor_with(self, slot: str, other_slot: str) -> bool:
        """True when two slots resolve to the same provider.

        The check `#7 <../../../../docs/v3/OPEN_DECISIONS.md>`_ turns on: a Red Team
        drawn from the same vendor as the Chair shares its blind spots, so this is worth
        *reporting* rather than forbidding — one vendor is a legitimate configuration
        while a second is being evaluated, and it should be visible that the diversity
        argument is not currently being had.
        """
        left = self.slot_assignments.get(require_slot(slot))
        right = self.slot_assignments.get(require_slot(other_slot))
        return bool(left) and left == right


def router_from_settings(
    registry: ProviderRegistry | None = None, cfg: "Settings | None" = None
) -> ModelRouter:
    """A router built from configuration. Every slot unassigned unless set.

    Reads ``v3_model_slot_<slot>`` settings, so assigning a slot is a configuration
    change and never a code change.
    """
    if cfg is None:
        from app.core.config import settings as cfg  # noqa: PLW0127

    assignments: dict[str, str] = {}
    for slot in sorted(MODEL_SLOTS):
        value = str(getattr(cfg, f"v3_model_slot_{slot}", "") or "").strip()
        if value:
            assignments[slot] = value
    return ModelRouter(
        registry=registry or ProviderRegistry(),
        slot_assignments=assignments,
        governance=default_governance(cfg),
    )


__all__ = [
    "DEGRADABLE_SLOTS",
    "NO_CONTENT",
    "DIVERSITY_PREFERRED_SLOTS",
    "MODEL_SLOTS",
    "SLOT_CHAIR",
    "SLOT_CHEAP_RESEARCH",
    "SLOT_CLASSIFICATION",
    "SLOT_DEEP_RESEARCH",
    "SLOT_DOCUMENT_REASONING",
    "SLOT_RED_TEAM",
    "SLOT_RESEARCH_DIRECTOR",
    "SLOT_TRANSLATION",
    "UNRESOLVED_GOVERNANCE_REFUSED",
    "UNRESOLVED_NOT_CONFIGURED",
    "UNRESOLVED_PROVIDER_NOT_REGISTERED",
    "ModelRouter",
    "ProviderRegistry",
    "SlotResolution",
    "require_slot",
    "router_from_settings",
]
