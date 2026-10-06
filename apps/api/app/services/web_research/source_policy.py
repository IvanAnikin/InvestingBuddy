"""The per-domain ``use_constraint`` registry — open-web W3 (spec §20.1).

*Not legal advice.* This records what the platform BELIEVES a publisher permits, so a
future public or commercial product can filter on it (``public_domain |
open_licence | commercial_permitted``). Every entry is a deliberate, reviewed claim;
anything not listed is ``unknown`` — the open-web default — and page signals (a
Creative Commons licence link, ``dc.rights``) can only speak for the page that carries
them.

Versioned: ``POLICY_VERSION`` changes whenever an entry does, and ingest records the
constraint on each version, so a later change never silently rewrites what an old
version was stored under.
"""

from __future__ import annotations

from dataclasses import dataclass

POLICY_VERSION = "2026-09-30.1"

USE_PUBLIC_DOMAIN = "public_domain"
USE_OPEN_LICENCE = "open_licence"
USE_PRIVATE_USE_PERMITTED = "private_use_permitted"
USE_COMMERCIAL_PERMITTED = "commercial_permitted"
USE_REDISTRIBUTION_PROHIBITED = "redistribution_prohibited"
USE_REQUIRES_LICENCE = "requires_licence"
USE_UNKNOWN = "unknown"

USE_CONSTRAINTS: frozenset[str] = frozenset(
    {
        USE_PUBLIC_DOMAIN,
        USE_OPEN_LICENCE,
        USE_PRIVATE_USE_PERMITTED,
        USE_COMMERCIAL_PERMITTED,
        USE_REDISTRIBUTION_PROHIBITED,
        USE_REQUIRES_LICENCE,
        USE_UNKNOWN,
    }
)


@dataclass(frozen=True)
class DomainPolicy:
    use_constraint: str
    #: ``CC-BY-4.0``, ``OGL-UK-3.0`` … when the constraint is a licence.
    licence_id: str | None = None
    note: str = ""


#: Registrable-domain (or host-suffix) → policy. Suffix match, most specific first.
DOMAIN_POLICIES: dict[str, DomainPolicy] = {
    # US federal works: 17 U.S.C. §105 — no copyright in works of the US Government.
    "usgs.gov": DomainPolicy(USE_PUBLIC_DOMAIN, note="US federal work (17 USC 105)"),
    "eia.gov": DomainPolicy(USE_PUBLIC_DOMAIN, note="US federal work (17 USC 105)"),
    "energy.gov": DomainPolicy(USE_PUBLIC_DOMAIN, note="US federal work (17 USC 105)"),
    "doe.gov": DomainPolicy(USE_PUBLIC_DOMAIN, note="US federal work (17 USC 105)"),
    "bls.gov": DomainPolicy(USE_PUBLIC_DOMAIN, note="US federal work (17 USC 105)"),
    "census.gov": DomainPolicy(USE_PUBLIC_DOMAIN, note="US federal work (17 USC 105)"),
    "bea.gov": DomainPolicy(USE_PUBLIC_DOMAIN, note="US federal work (17 USC 105)"),
    "commerce.gov": DomainPolicy(USE_PUBLIC_DOMAIN, note="US federal work (17 USC 105)"),
    "treasury.gov": DomainPolicy(USE_PUBLIC_DOMAIN, note="US federal work (17 USC 105)"),
    "congress.gov": DomainPolicy(USE_PUBLIC_DOMAIN, note="US federal work (17 USC 105)"),
    "whitehouse.gov": DomainPolicy(USE_PUBLIC_DOMAIN, note="US federal work (17 USC 105)"),
    "defense.gov": DomainPolicy(USE_PUBLIC_DOMAIN, note="US federal work (17 USC 105)"),
    "nist.gov": DomainPolicy(USE_PUBLIC_DOMAIN, note="US federal work (17 USC 105)"),
    "noaa.gov": DomainPolicy(USE_PUBLIC_DOMAIN, note="US federal work (17 USC 105)"),
    "epa.gov": DomainPolicy(USE_PUBLIC_DOMAIN, note="US federal work (17 USC 105)"),
    "usda.gov": DomainPolicy(USE_PUBLIC_DOMAIN, note="US federal work (17 USC 105)"),
    "ferc.gov": DomainPolicy(USE_PUBLIC_DOMAIN, note="US federal work (17 USC 105)"),
    # Open government licences.
    "gov.uk": DomainPolicy(USE_OPEN_LICENCE, "OGL-UK-3.0", "Open Government Licence v3"),
    "europa.eu": DomainPolicy(
        USE_OPEN_LICENCE, "CC-BY-4.0", "Commission reuse policy (Decision 2011/833/EU)"
    ),
    "worldbank.org": DomainPolicy(USE_OPEN_LICENCE, "CC-BY-4.0", "World Bank open access"),
    # Owner decisions already in force (private use only).
    "fca.org.uk": DomainPolicy(USE_PRIVATE_USE_PERMITTED, note="FCA NSM terms; owner decision"),
    "asx.com.au": DomainPolicy(USE_PRIVATE_USE_PERMITTED, note="ASX terms; owner decision"),
}

_CC_ZERO = ("creativecommons.org/publicdomain/zero", "creativecommons.org/publicdomain/mark")
_CC_LICENCE_PREFIX = "creativecommons.org/licenses/"


@dataclass(frozen=True)
class UseDecision:
    use_constraint: str
    licence_id: str | None
    basis: str  # ``domain_policy`` | ``page_signal`` | ``default``


def _match(host: str) -> DomainPolicy | None:
    host = (host or "").lower().strip(".").removeprefix("www.")
    best: tuple[int, DomainPolicy] | None = None
    for suffix, policy in DOMAIN_POLICIES.items():
        if host == suffix or host.endswith("." + suffix):
            if best is None or len(suffix) > best[0]:
                best = (len(suffix), policy)
    return best[1] if best else None


def licence_from_signals(signals: tuple[str, ...] | list[str]) -> tuple[str, str | None] | None:
    """A Creative Commons licence named by the page itself, or None.

    Non-commercial variants are recorded as ``private_use_permitted``, never as an open
    licence, so a later commercial filter cannot admit them.
    """
    for raw in signals or ():
        text = (raw or "").lower()
        if any(marker in text for marker in _CC_ZERO):
            return USE_PUBLIC_DOMAIN, "CC0-1.0"
        index = text.find(_CC_LICENCE_PREFIX)
        if index == -1:
            continue
        rest = text[index + len(_CC_LICENCE_PREFIX):].split("?")[0].strip("/")
        parts = [p for p in rest.split("/") if p]
        if not parts:
            continue
        kind = parts[0]
        version = parts[1] if len(parts) > 1 else ""
        licence_id = f"CC-{kind.upper()}" + (f"-{version}" if version else "")
        if "nc" in kind.split("-"):
            return USE_PRIVATE_USE_PERMITTED, licence_id
        return USE_OPEN_LICENCE, licence_id
    return None


def use_constraint_for(host: str | None, *, licence_signals: tuple[str, ...] = ()) -> UseDecision:
    """The recorded constraint for a document on ``host`` (registry, then page, then unknown)."""
    policy = _match(host or "")
    if policy is not None:
        return UseDecision(policy.use_constraint, policy.licence_id, "domain_policy")
    from_page = licence_from_signals(licence_signals)
    if from_page is not None:
        return UseDecision(from_page[0], from_page[1], "page_signal")
    return UseDecision(USE_UNKNOWN, None, "default")


__all__ = [
    "DOMAIN_POLICIES",
    "POLICY_VERSION",
    "USE_COMMERCIAL_PERMITTED",
    "USE_CONSTRAINTS",
    "USE_OPEN_LICENCE",
    "USE_PRIVATE_USE_PERMITTED",
    "USE_PUBLIC_DOMAIN",
    "USE_REDISTRIBUTION_PROHIBITED",
    "USE_REQUIRES_LICENCE",
    "USE_UNKNOWN",
    "DomainPolicy",
    "UseDecision",
    "licence_from_signals",
    "use_constraint_for",
]
