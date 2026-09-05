"""Which provider may see which content — V3.4 Slice 4.1.

The rule that has to hold before any adapter is written: **a provider is allowed to see
a class of content, or it is not, and the default is not.**

WHY THIS IS PER PROVIDER AND PER CLASS, NOT A FLAG
==================================================
`OPEN DECISION #11 <../../../../docs/v3/OPEN_DECISIONS.md>`_ is user-owned and its
recommendation is explicit that **both** dimensions are required: "external models are
allowed" and "this provider, in this jurisdiction, under these retention terms, is
allowed" are different questions. A single per-run flag is exactly the coarse control
that leaks one document.

So this is a matrix, it defaults to deny, and it is enforced at the point a payload is
assembled rather than by a convention in a docstring.

DEEPSEEK IS THE WORKED EXAMPLE, AND IT IS RESTRICTED
====================================================
`OPEN DECISION #4 <../../../../docs/v3/OPEN_DECISIONS.md>`_ is open and provisionally
restricts DeepSeek to public content until its data-handling, jurisdiction and retention
terms are read and recorded. Note what the strategy document establishes alongside that:
the *price* case is weaker than assumed — at InvestingBuddy's call shape DeepSeek is
1.4x cheaper off-peak and **30% more expensive at peak**, and runs are user-triggered so
off-peak cannot be chosen. **Cheap does not override governance**, and here it does not
even buy much.

WHAT "UNKNOWN PROVIDER" MEANS
============================
Deny. A provider nobody has recorded a policy for is not a provider with a permissive
default — it is a provider nobody has evaluated, and the first time that matters is the
time it receives something it should not have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.services.corpus.policy import (
    ACCESS_CLASSES,
    ACCESS_DERIVED,
    ACCESS_LICENSED_PRIVATE,
    ACCESS_PUBLIC_ISSUER,
    ACCESS_PUBLIC_OFFICIAL,
    ACCESS_PUBLIC_WEB,
    ACCESS_USER_PRIVATE,
    PUBLIC_ACCESS_CLASSES,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings

#: The classes a provider gets when nothing has been recorded about it: **none**.
DENY_ALL: frozenset[str] = frozenset()

#: The public classes. The most any provider may have until a decision is recorded.
PUBLIC_ONLY: frozenset[str] = frozenset(PUBLIC_ACCESS_CLASSES)

#: Classes that may never travel to an external provider under the current default.
#: `#11 <../../../../docs/v3/OPEN_DECISIONS.md>`_ is user-owned and the default is deny,
#: so an allowlist entry naming one of these is refused rather than honoured — an
#: accidental configuration change must not be able to authorise it.
NEVER_EXTERNAL_BY_DEFAULT: frozenset[str] = frozenset(
    {ACCESS_LICENSED_PRIVATE, ACCESS_USER_PRIVATE}
)


@dataclass(frozen=True)
class ProviderPolicy:
    """What one provider may receive, and on whose authority."""

    provider_id: str
    allowed_access_classes: frozenset[str] = DENY_ALL
    #: Free text naming the decision that granted this. A policy with no authority
    #: recorded is a policy nobody can audit.
    authority: str | None = None
    #: True when the provider runs outside the platform's own infrastructure.
    is_external: bool = True
    note: str | None = None

    def __post_init__(self) -> None:
        unknown = set(self.allowed_access_classes) - set(ACCESS_CLASSES)
        if unknown:
            raise ValueError(
                f"{self.provider_id}: {sorted(unknown)} are not recognised access "
                "classes. An unrecognised class is never treated as public."
            )
        if self.is_external:
            forbidden = set(self.allowed_access_classes) & NEVER_EXTERNAL_BY_DEFAULT
            if forbidden:
                raise ValueError(
                    f"{self.provider_id}: {sorted(forbidden)} may not be granted to an "
                    "external provider. OPEN DECISION #11 is user-owned and its default "
                    "is deny; a per-document AND per-provider policy has to exist first, "
                    "and it cannot be created by editing an allowlist."
                )
        private = set(self.allowed_access_classes) & NEVER_EXTERNAL_BY_DEFAULT
        if private and "#11" not in (self.authority or ""):
            # Applies to an internal provider too. "It runs in our tenancy" is a
            # statement about where it runs, not an answer to whether private material
            # may be sent to it — and #11 is the decision that answers that.
            raise ValueError(
                f"{self.provider_id}: granting {sorted(private)} requires an authority "
                "that names OPEN DECISION #11, which is the decision that governs "
                "private content reaching a model at all. Where the provider runs is a "
                "different question from whether it may see this."
            )
        if self.allowed_access_classes and not (self.authority or "").strip():
            raise ValueError(
                f"{self.provider_id}: a policy that grants anything must record the "
                "authority that granted it. A grant nobody can audit is a grant nobody "
                "reviewed."
            )

    def permits(self, access_class: str | None) -> bool:
        return (access_class or "") in self.allowed_access_classes

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "allowed_access_classes": sorted(self.allowed_access_classes),
            "authority": self.authority,
            "is_external": self.is_external,
            "note": self.note,
        }


@dataclass
class ProviderGovernance:
    """The matrix. Deny by default, per provider and per class."""

    policies: dict[str, ProviderPolicy] = field(default_factory=dict)

    def register(self, policy: ProviderPolicy) -> ProviderPolicy:
        self.policies[policy.provider_id] = policy
        return policy

    def policy_for(self, provider_id: str) -> ProviderPolicy:
        """The recorded policy, or a **deny-all** one for an unrecorded provider.

        A provider nobody has recorded a policy for is not a provider with a permissive
        default — it is a provider nobody has evaluated.
        """
        found = self.policies.get((provider_id or "").strip())
        if found is not None:
            return found
        return ProviderPolicy(provider_id=provider_id or "unknown")

    def permits(self, provider_id: str, access_class: str | None) -> bool:
        return self.policy_for(provider_id).permits(access_class)

    def refuse_reason(self, provider_id: str, access_class: str | None) -> str | None:
        """``None`` when permitted, else a sentence naming what was refused and why."""
        policy = self.policy_for(provider_id)
        if policy.permits(access_class):
            return None
        if not policy.allowed_access_classes:
            return (
                f"no data-governance policy is recorded for provider "
                f"{provider_id!r}, so it may receive nothing. An unevaluated provider "
                "is not a permissive one."
            )
        return (
            f"provider {provider_id!r} may receive "
            f"{sorted(policy.allowed_access_classes)} and this content is "
            f"{access_class!r}"
        )

    def assert_permitted(self, provider_id: str, access_class: str | None) -> None:
        """Raise ``ProviderNotPermittedError`` unless the class is allowed."""
        reason = self.refuse_reason(provider_id, access_class)
        if reason is not None:
            raise ProviderNotPermittedError(provider_id, access_class, reason)

    def filter_permitted(
        self, provider_id: str, items: list[tuple[str, str | None]]
    ) -> tuple[list[str], list[tuple[str, str | None]]]:
        """Split ``(payload, access_class)`` pairs into permitted and refused.

        Returns ``(permitted_payloads, refused_pairs)``. Splitting rather than raising
        is what lets a run send the public half of an evidence pack to a cheap provider
        and keep the rest local, which is the whole practical value of a per-class
        policy — and the refused half is returned so the caller can say what it withheld
        instead of silently sending less.
        """
        permitted: list[str] = []
        refused: list[tuple[str, str | None]] = []
        for payload, access_class in items:
            if self.permits(provider_id, access_class):
                permitted.append(payload)
            else:
                refused.append((payload, access_class))
        return permitted, refused


class ProviderNotPermittedError(PermissionError):
    """Content of this class may not be sent to this provider."""

    def __init__(
        self, provider_id: str, access_class: str | None, reason: str
    ) -> None:
        super().__init__(reason)
        self.provider_id = provider_id
        self.access_class = access_class
        self.reason = reason
    #: Permanent for the worker taxonomy: a governance refusal will refuse again.
    job_transient = False


def default_governance(cfg: "Settings | None" = None) -> ProviderGovernance:
    """The governance matrix as it stands while the open decisions are open.

    Every external provider is **public-only**, on the authority of the open decisions
    themselves. Azure OpenAI is the incumbent and is the one provider already carrying
    the platform's own content under the existing deployment, so it is recorded as
    non-external — which is a statement about where it runs, not a licence to widen it.

    Nothing here is a decision. Each entry cites the decision that constrains it, and a
    test asserts no external provider has been granted a private class.
    """
    governance = ProviderGovernance()
    governance.register(
        ProviderPolicy(
            provider_id="azure_openai",
            allowed_access_classes=frozenset(
                {
                    ACCESS_PUBLIC_OFFICIAL,
                    ACCESS_PUBLIC_ISSUER,
                    ACCESS_PUBLIC_WEB,
                    ACCESS_DERIVED,
                }
            ),
            authority=(
                "incumbent provider under the existing deployment; runs inside the "
                "platform's own Azure tenancy"
            ),
            is_external=False,
            note="OPEN DECISION #5 governs which model fills which slot, not access.",
        )
    )
    for provider_id, decision in (
        ("deepseek", "#4 (data governance, user-owned) — provisionally public-only"),
        ("openai", "#5 (model routing) — public-only until a policy is recorded"),
        ("gemini", "#6 (deep research role) — public-only until a policy is recorded"),
        ("claude", "#7 (red team role) — public-only until a policy is recorded"),
        ("exa", "#3 (search provider) — a search index only ever sees a query"),
        ("perplexity", "#3 (search provider) — a search index only ever sees a query"),
    ):
        governance.register(
            ProviderPolicy(
                provider_id=provider_id,
                allowed_access_classes=PUBLIC_ONLY,
                authority=f"OPEN DECISION {decision}",
                is_external=True,
            )
        )
    return governance


__all__ = [
    "DENY_ALL",
    "NEVER_EXTERNAL_BY_DEFAULT",
    "PUBLIC_ONLY",
    "ProviderGovernance",
    "ProviderNotPermittedError",
    "ProviderPolicy",
    "default_governance",
]
