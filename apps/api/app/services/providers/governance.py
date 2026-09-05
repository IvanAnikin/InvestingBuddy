"""Which provider may see which content — V3.4 Slice 4.1.

The rule that has to hold before any adapter is written: **a provider is allowed to see
a class of content, or it is not, and the default is not.**

TWO GATES, AND BOTH MUST OPEN
=============================
`#11 and #4 were resolved 2026-09-05 <../../../../docs/v3/OPEN_DECISIONS.md>`_
(ADR-049), and the resolution is explicit that **both** dimensions are required:

1. **The coarse gate — provider × access class.** Recorded here. It answers "has anybody
   evaluated this provider for this kind of material at all", and it denies by default.
2. **The fine gate — the document's own rights.** Recorded on
   ``corpus.policy.ArtifactPolicy`` as ``sent_to_external_model`` plus
   ``permitted_providers``. It answers "does *this* document's licence permit *this*
   provider".

A document that may reach some external model is not thereby a document that may reach
every one of them, so ``permits_document`` requires both. Checking only the coarse gate
would send a licensed report to a provider its licence names nobody for; checking only
the fine gate would send material to a provider nobody evaluated.

GEOGRAPHY IS NOT THE RULE; RIGHTS ARE
=====================================
ADR-049 settled the question the previous version of this module got wrong. DeepSeek's
China location and storage are **not** a blocker for this project, and the blanket
``user_private → DENY`` rule that stood here was a placeholder for a decision nobody had
taken rather than a considered policy. What constrains use is the document's licence and
its explicit policy — so DeepSeek may process public content, derived research context,
and user-private content **when that document says so**.

A SECRET IS NOT AN ACCESS CLASS, AND NEVER BECOMES ONE
======================================================
API keys, passwords, credentials, tokens and private system configuration are excluded
**categorically**, not by policy — because a policy is something somebody can edit, and
the one thing that must not be widenable by an allowlist change is a credential. There is
no access class that represents them and ``assert_no_credentials`` refuses them
regardless of what any policy says.

WHAT "UNKNOWN PROVIDER" MEANS
============================
Deny. A provider nobody has recorded a policy for is not a provider with a permissive
default — it is a provider nobody has evaluated, and the first time that matters is the
time it receives something it should not have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

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

#: The classes whose grant requires an authority citing ADR-049. Granting one says only
#: that the provider has been *evaluated* for that kind of material; the document's own
#: rights are the second gate and default to closed.
PRIVATE_ACCESS_CLASSES: frozenset[str] = frozenset(
    {ACCESS_LICENSED_PRIVATE, ACCESS_USER_PRIVATE}
)

#: Retained under its old name for readers of the pre-ADR-049 code and tests: it is the
#: same set, and what changed is that a grant is now possible with a cited authority
#: rather than impossible.
NEVER_EXTERNAL_BY_DEFAULT: frozenset[str] = PRIVATE_ACCESS_CLASSES

#: Substrings that count as citing the decision. Deliberately narrow — "the user said it
#: was fine" is not an authority.
_DECISION_CITATIONS: tuple[str, ...] = ("ADR-049", "#11")


def _names_the_decision(authority: str | None) -> bool:
    return any(token in (authority or "") for token in _DECISION_CITATIONS)


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
        private = set(self.allowed_access_classes) & PRIVATE_ACCESS_CLASSES
        if private and not _names_the_decision(self.authority):
            # Applies to an internal provider too. "It runs in our tenancy" is a
            # statement about where it runs, not an answer to whether private material
            # may be sent to it. ADR-049 is the decision that answers that, and a grant
            # of a private class has to cite it — which is what keeps this auditable
            # after the resolution rather than merely permitted by it.
            #
            # Note what this does NOT do: granting a private class here does not send
            # anything. It only says the provider has been evaluated for that KIND of
            # material. The document's own rights are the second gate and they default
            # to closed.
            raise ValueError(
                f"{self.provider_id}: granting {sorted(private)} requires an authority "
                "that names ADR-049 or OPEN DECISION #11 — the decision that governs "
                "private content reaching a model. Where the provider runs is a "
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

    def permits_document(self, provider_id: str, policy: Any) -> bool:
        """Whether this provider may receive this **document**. Both gates.

        ``policy`` is a ``corpus.policy.ArtifactPolicy``; it is duck-typed so this module
        keeps no import of the corpus.
        """
        return self.refuse_document_reason(provider_id, policy) is None

    def refuse_document_reason(self, provider_id: str, policy: Any) -> str | None:
        """``None`` when permitted, else which gate closed and why.

        The coarse gate is checked **first** on purpose. "Nobody has evaluated this
        provider for licensed material" is a more fundamental answer than "this
        particular licence does not name it", and reporting the deeper one is more useful
        to whoever has to fix it.
        """
        access_class = getattr(policy, "access_class", None)
        coarse = self.refuse_reason(provider_id, access_class)
        if coarse is not None:
            return coarse
        fine = getattr(policy, "refuse_provider_reason", None)
        if fine is None:
            # A policy object that cannot answer the question is not a permissive one.
            return (
                f"the supplied policy for {access_class!r} cannot state whether "
                f"{provider_id!r} may read it, so it may not"
            )
        return fine(provider_id)

    def assert_document_permitted(self, provider_id: str, policy: Any) -> None:
        reason = self.refuse_document_reason(provider_id, policy)
        if reason is not None:
            raise ProviderNotPermittedError(
                provider_id, getattr(policy, "access_class", None), reason
            )

    def filter_permitted_documents(
        self, provider_id: str, documents: list[tuple[str, Any]]
    ) -> tuple[list[str], list[tuple[str, str]]]:
        """Split ``(payload, policy)`` pairs into permitted payloads and refusals.

        Returns ``(permitted, [(payload, reason), …])``. The refused half comes back with
        **its reason** rather than only a count, so a run can say *what* it withheld and
        *why* — which is the difference between an honest partial payload and a silently
        smaller one.
        """
        permitted: list[str] = []
        refused: list[tuple[str, str]] = []
        for payload, policy in documents:
            reason = self.refuse_document_reason(provider_id, policy)
            if reason is None:
                permitted.append(payload)
            else:
                refused.append((payload, reason))
        return permitted, refused

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


#: Fragments that mean a payload is carrying a credential. Matched case-insensitively
#: against the payload's own text, and deliberately broad: a false positive costs a
#: refusal a human can override by removing the secret, and a false negative costs a key.
_CREDENTIAL_MARKERS: tuple[str, ...] = (
    "api_key",
    "api-key",
    "apikey",
    "secret_key",
    "client_secret",
    "password",
    "passwd",
    "authorization: bearer",
    "bearer ey",
    "private_key",
    "-----begin",
    "connectionstring",
    "connection_string",
    "accountkey=",
    "sas_token",
    "access_token",
    "refresh_token",
    "aws_secret",
)


class CredentialInPayloadError(PermissionError):
    """A payload bound for a provider appears to carry a credential.

    Raised **regardless of any policy**, because a secret is not an access class and
    never becomes one. A policy is something somebody can edit; the one thing that must
    not be widenable by an allowlist change is a credential.
    """

    job_transient = False

    def __init__(self, marker: str) -> None:
        super().__init__(
            f"payload appears to contain a credential ({marker!r}) and will not be sent "
            "to any provider. This is not a policy decision and cannot be overridden by "
            "one — see ADR-049."
        )
        self.marker = marker


def assert_no_credentials(payload: str | None) -> None:
    """Refuse a payload that looks like it carries a secret. Categorical.

    Not a substitute for not putting credentials in a payload; a backstop, in the same
    spirit as ``assert_registry_safe`` and the ``RedactingFilter`` that exists because
    root-level INFO logging once leaked an EODHD ``api_token``.
    """
    text = (payload or "").casefold()
    for marker in _CREDENTIAL_MARKERS:
        if marker in text:
            raise CredentialInPayloadError(marker)


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
                    ACCESS_USER_PRIVATE,
                    ACCESS_LICENSED_PRIVATE,
                }
            ),
            authority=(
                "ADR-050 (resolves OPEN DECISION #5, 2026-09-05): the existing Azure "
                "deployment is the strong-model fallback and the only OpenAI-family "
                "dependency. Private classes per ADR-049 — evaluated, and still gated "
                "by each document's own rights."
            ),
            is_external=False,
            note=(
                "Runs inside the platform's own Azure tenancy, which is a statement "
                "about WHERE it runs and not a licence to widen what it may see."
            ),
        )
    )
    # DeepSeek is the PRIMARY external research provider (ADR-048/049). It is evaluated
    # for every class including the private ones — which grants nothing on its own: the
    # document's own rights are the second gate and default to closed.
    governance.register(
        ProviderPolicy(
            provider_id="deepseek",
            allowed_access_classes=frozenset(
                {
                    ACCESS_PUBLIC_OFFICIAL,
                    ACCESS_PUBLIC_ISSUER,
                    ACCESS_PUBLIC_WEB,
                    ACCESS_DERIVED,
                    ACCESS_USER_PRIVATE,
                    ACCESS_LICENSED_PRIVATE,
                }
            ),
            authority=(
                "ADR-049 (resolves OPEN DECISION #4 and #11, 2026-09-05): approved as "
                "the primary external research provider; China location/storage is not "
                "a blocker, and document rights govern rather than provider geography"
            ),
            is_external=True,
            note=(
                "Evaluated for private classes. Whether any PARTICULAR private document "
                "may be sent is decided by that document's own rights, which default to "
                "closed and require an auditable rationale to widen."
            ),
        )
    )
    # Deferred and not activated, each for a recorded reason. Kept in the matrix so the
    # register is a complete statement rather than a list of whatever happens to be on.
    for provider_id, decision in (
        (
            "openai",
            "ADR-050 — NOT USED: no new OpenAI commercial account; the existing Azure "
            "deployment is the only OpenAI-family dependency",
        ),
        ("gemini", "ADR-048 round — DEFERRED / NOT ACTIVATED (#6)"),
        (
            "claude",
            "ADR-050 round — Claude is not a production provider (#7); Claude Code is a "
            "development tool and must not become a runtime dependency",
        ),
        ("exa", "ADR-048 — DEFERRED; DeepSeek web_search is the primary search path"),
        ("perplexity", "ADR-048 — DEFERRED; DeepSeek web_search is the primary path"),
    ):
        governance.register(
            ProviderPolicy(
                provider_id=provider_id,
                allowed_access_classes=PUBLIC_ONLY,
                authority=decision,
                is_external=True,
                note="Deferred: no credentials, no adapter, nothing depends on it.",
            )
        )
    return governance


__all__ = [
    "DENY_ALL",
    "PRIVATE_ACCESS_CLASSES",
    "CredentialInPayloadError",
    "assert_no_credentials",
    "NEVER_EXTERNAL_BY_DEFAULT",
    "PUBLIC_ONLY",
    "ProviderGovernance",
    "ProviderNotPermittedError",
    "ProviderPolicy",
    "default_governance",
]
