"""Which model fills which slot, at run time — V3.10 Slice 10.2.

THE RULE THAT DOES NOT CHANGE
=============================
> **No domain module names a model. It names a slot.**

Established in V3.4.1 and unchanged. What 10.2 adds is a resolver that turns a slot into
a *working client* using whatever is actually configured, and says which one it used.

THE ROUTING, AND WHY
====================
[ADR-050](../../../docs/DECISIONS.md): cheap and bulk research goes to DeepSeek, difficult
final reasoning to the existing Azure OpenAI deployment.

===================  ===================================================
slot                 preferred        fallback
===================  ===================================================
investigator         DeepSeek         Azure OpenAI
follow-up            DeepSeek         Azure OpenAI
red team             Azure OpenAI     DeepSeek
chair                Azure OpenAI     (none — the deterministic chair)
===================  ===================================================

**Red Team prefers a different vendor from the Chair** where both exist, because a model
challenging its own family's output shares its blind spots and an independent second
opinion is most of the value of running a challenge at all. Where only one vendor is
configured that preference cannot be honoured, and ``shares_vendor_with_chair`` **says so**
rather than letting a reader assume diversity that is not there.

DEGRADING IS PART OF THE CONTRACT
=================================
A slot with nothing configured resolves to ``None``, and every caller keeps its
deterministic path — the property V3.0 established with the chair fallback and the reason
a provider outage is a worse answer rather than no answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings

SLOT_INVESTIGATOR = "investigator"
SLOT_FOLLOW_UP = "follow_up"
SLOT_RED_TEAM = "red_team"
SLOT_CHAIR = "chair"

SLOTS: tuple[str, ...] = (SLOT_INVESTIGATOR, SLOT_FOLLOW_UP, SLOT_RED_TEAM, SLOT_CHAIR)

VENDOR_DEEPSEEK = "deepseek"
VENDOR_AZURE_OPENAI = "azure_openai"

#: Preference order per slot. First one that resolves wins.
DEFAULT_PREFERENCES: dict[str, tuple[str, ...]] = {
    SLOT_INVESTIGATOR: (VENDOR_DEEPSEEK, VENDOR_AZURE_OPENAI),
    SLOT_FOLLOW_UP: (VENDOR_DEEPSEEK, VENDOR_AZURE_OPENAI),
    SLOT_RED_TEAM: (VENDOR_AZURE_OPENAI, VENDOR_DEEPSEEK),
    SLOT_CHAIR: (VENDOR_AZURE_OPENAI,),
}


@dataclass(frozen=True)
class ResolvedSlot:
    """A slot, the vendor that filled it, and the client — or an honest absence."""

    slot: str
    vendor: str | None
    client: Any = None
    reason: str | None = None

    @property
    def resolved(self) -> bool:
        return self.client is not None

    def to_dict(self) -> dict[str, Any]:
        return {"slot": self.slot, "vendor": self.vendor, "reason": self.reason}


def _azure_client(cfg: "Settings") -> Any:
    """The existing council client. Returns None rather than raising — the factory's
    own contract, and the reason a missing credential keeps the deterministic path."""
    # `get_llm_client` gates on `llm_council_enabled` and on the COUNCIL provider
    # setting. V3 asks a narrower question — "is an Azure deployment reachable" — so it
    # is asked directly rather than by flipping a V2 flag that also changes V2 behaviour.
    if not (cfg.azure_openai_api_key and cfg.azure_openai_endpoint):
        return None
    try:
        from app.services.llm.azure_openai_client import AzureOpenAILLMClient

        return AzureOpenAILLMClient(cfg)
    except Exception:  # noqa: BLE001 - a missing optional dependency is "unavailable"
        return None


def _deepseek_client(cfg: "Settings") -> Any:
    """A DeepSeek model client, or ``None`` unless it is BOTH enabled and credentialed.

    The flag is checked first and deliberately: a credential appearing in the
    environment is not a decision to route research through that vendor. V3.11's whole
    acceptance — three Councils, the cost measurement — was taken on Azure OpenAI, and a
    key dropped into ``.env`` silently moved the Investigator off it.
    """
    from app.integrations.deepseek.providers import DeepSeekModelProvider
    from app.integrations.deepseek.transport import transport_from_settings

    if not getattr(cfg, "v3_deepseek_model_enabled", False):
        return None
    transport = transport_from_settings(cfg)
    if transport is None:
        return None
    return DeepSeekModelProvider(transport=transport)


_BUILDERS = {VENDOR_AZURE_OPENAI: _azure_client, VENDOR_DEEPSEEK: _deepseek_client}


@dataclass
class ModelRouting:
    """Every slot resolved once, for one run."""

    slots: dict[str, ResolvedSlot]

    def client_for(self, slot: str) -> Any:
        resolved = self.slots.get(slot)
        return resolved.client if resolved is not None else None

    def vendor_for(self, slot: str) -> str | None:
        resolved = self.slots.get(slot)
        return resolved.vendor if resolved is not None else None

    @property
    def shares_vendor_with_chair(self) -> bool:
        """True when the Red Team and the Chair are the same vendor.

        Reported rather than prevented. A single-vendor environment cannot honour the
        preference, and a reader who assumed diversity that is not there would over-weight
        a challenge that shares the Chair's blind spots.
        """
        red = self.vendor_for(SLOT_RED_TEAM)
        chair = self.vendor_for(SLOT_CHAIR)
        return bool(red and chair and red == chair)

    @property
    def any_resolved(self) -> bool:
        return any(s.resolved for s in self.slots.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "slots": {k: v.to_dict() for k, v in sorted(self.slots.items())},
            "red_team_shares_vendor_with_chair": self.shares_vendor_with_chair,
        }


def resolve_routing(
    cfg: "Settings", *, preferences: "dict[str, tuple[str, ...]] | None" = None
) -> ModelRouting:
    """Resolve every slot against what is actually configured.

    Deliberately builds a client per slot rather than sharing one: the existing
    ``LLMClient`` accumulates usage on the instance and pops it on
    ``consume_usage``, so two slots sharing an instance would attribute one's tokens to
    the other — and per-slot cost is the whole point of measuring it.
    """
    prefs = preferences or DEFAULT_PREFERENCES
    slots: dict[str, ResolvedSlot] = {}
    for slot in SLOTS:
        chosen: ResolvedSlot | None = None
        tried: list[str] = []
        for vendor in prefs.get(slot, ()):
            builder = _BUILDERS.get(vendor)
            if builder is None:
                continue
            tried.append(vendor)
            client = builder(cfg)
            if client is not None:
                chosen = ResolvedSlot(slot=slot, vendor=vendor, client=client)
                break
        slots[slot] = chosen or ResolvedSlot(
            slot=slot,
            vendor=None,
            reason=(
                f"no configured vendor for this slot (tried: {tried or 'none'}); "
                "the caller keeps its deterministic path"
            ),
        )
    return ModelRouting(slots=slots)


__all__ = [
    "DEFAULT_PREFERENCES",
    "SLOTS",
    "SLOT_CHAIR",
    "SLOT_FOLLOW_UP",
    "SLOT_INVESTIGATOR",
    "SLOT_RED_TEAM",
    "VENDOR_AZURE_OPENAI",
    "VENDOR_DEEPSEEK",
    "ModelRouting",
    "ResolvedSlot",
    "resolve_routing",
]
