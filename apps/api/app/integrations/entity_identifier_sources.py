"""Live identifier sources — GLEIF and SEC EDGAR — V3.2 Slice 2.3.1.

Adapters from the two free registries the platform already talks to onto the
``IdentifierSource`` contract in ``app.services.entities.claims``.

WHY THEY LIVE HERE AND NOT IN ``app/services/entities/``
========================================================
A test from slice 2.1 parses every module in that package and fails if one imports
a network client. That is not an obstacle to route around — it is the design.
``entities`` holds identity *semantics*; ``integrations`` holds clients; the Protocol
crosses the boundary in the safe direction, and the orchestration
(``entities.promotion``) receives sources rather than importing them.

EVERY PROVIDER IS INJECTED
==========================
Both adapters take their provider as a constructor argument, duck-typed, so the unit
suite drives them with a fake that makes no network call — the same shape as the
corpus artifact store's injected client factory. A live contract test is marked
``integration`` and gated on ``ENABLE_INTEGRATION_TESTS``. Both registries are free
and unauthenticated, so there is nothing to spend; there is still a rate limit, which
is why nothing here loops.

THE RULE BOTH ADAPTERS OBEY
===========================
**Withhold rather than assert.** GLEIF's name filter is a PARTIAL match, so
searching "Pandora" returns every legal name containing it. Emitting those as claims
would let the verification gate accept whichever LEI happens to be unheld — the gate
can detect a LEI already held by another subject, and cannot detect one belonging to
a company nobody has ingested. That is a silent misattribution of an entire filing
history, so an ambiguous match produces **no claim and a stated reason**.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.integrations.providers.sec_edgar_fundamentals import (
    SecExchangeNotSupportedError,
)
from app.services.entities.claims import (
    WITHHELD_AMBIGUOUS_NAME_MATCH,
    WITHHELD_NOT_FOUND,
    WITHHELD_NOT_SUPPORTED,
    WITHHELD_PARTIAL_NAME_MATCH,
    WITHHELD_SOURCE_ERROR,
    WITHHELD_VENUE_NOT_SEC_ELIGIBLE,
    IdentifierClaim,
    IdentifierQuery,
    SourceLookupResult,
    WithheldFinding,
)
from app.services.entities.identifiers import SCHEME_CIK, SCHEME_LEI
from app.services.entities.master import normalize_entity_name

GLEIF_SOURCE_ID = "gleif"
SEC_SOURCE_ID = "sec_edgar"

#: A registry confirming its own identifier. Nothing is more direct than this.
_CONFIDENCE_REGISTRY_CONFIRMED = 1.0
#: An exact match on a legal name in the registry. Deliberately **not** 1.0: an
#: exact string match on a name is still a name, and two entities may legally share
#: one. The resolver treats a name as weak evidence for the same reason.
_CONFIDENCE_EXACT_NAME = 0.9

_MAX_NAME_HITS = 5


@dataclass
class GleifIdentifierSource:
    """LEI from GLEIF — the authoritative registry, supervised by the FSB.

    ``provider`` must expose ``get_by_lei(lei)`` and
    ``search_by_name(name, page_size)``; ``app.integrations.providers.gleif_provider.GleifProvider``
    does. It is injected so the unit suite can drive this with a fake.
    """

    provider: Any
    source_id: str = GLEIF_SOURCE_ID
    schemes: frozenset[str] = field(default_factory=lambda: frozenset({SCHEME_LEI}))

    async def lookup(self, query: IdentifierQuery) -> SourceLookupResult:
        result = SourceLookupResult(source_id=self.source_id)

        known = (query.known_identifiers or {}).get(SCHEME_LEI)
        if known:
            return await self._confirm_known_lei(known, result)

        if not query.legal_name:
            # GLEIF is jurisdiction-based and has no ticker index. Deriving a name
            # from a ticker so this could answer is how `BA` becomes Boeing.
            result.withheld.append(
                WithheldFinding(
                    scheme=SCHEME_LEI,
                    reason=WITHHELD_NOT_SUPPORTED,
                    detail=(
                        "GLEIF has no ticker index; it needs a legal name or a LEI. "
                        "A name guessed from a ticker is not a lookup."
                    ),
                )
            )
            return result

        return await self._search_by_name(query.legal_name, result)

    async def _confirm_known_lei(
        self, lei: str, result: SourceLookupResult
    ) -> SourceLookupResult:
        try:
            profile = await self.provider.get_by_lei(lei)
        except ValueError as exc:
            # The provider raises ValueError for a 404. Not found is a real answer
            # about the world, and it is recorded rather than dropped.
            result.withheld.append(
                WithheldFinding(
                    scheme=SCHEME_LEI,
                    reason=WITHHELD_NOT_FOUND,
                    detail=f"GLEIF has no record for {lei.upper()}: {exc}",
                )
            )
            return result
        except Exception as exc:  # noqa: BLE001 - a failure is not a "no result"
            result.withheld.append(
                WithheldFinding(
                    scheme=SCHEME_LEI,
                    reason=WITHHELD_SOURCE_ERROR,
                    detail=f"GLEIF lookup failed: {type(exc).__name__}: {exc}",
                )
            )
            return result

        value = getattr(profile, "lei", None)
        if not value:
            result.withheld.append(
                WithheldFinding(
                    scheme=SCHEME_LEI,
                    reason=WITHHELD_SOURCE_ERROR,
                    detail="GLEIF returned a record with no LEI",
                )
            )
            return result

        result.claims.append(
            IdentifierClaim(
                scheme=SCHEME_LEI,
                value=value,
                source=self.source_id,
                source_url=getattr(profile, "source_url", None),
                confidence=_CONFIDENCE_REGISTRY_CONFIRMED,
                detail=(
                    "confirmed against the GLEIF record for this LEI; legal name "
                    f"{getattr(profile, 'legal_name', None)!r}"
                ),
            )
        )
        return result

    async def _search_by_name(
        self, legal_name: str, result: SourceLookupResult
    ) -> SourceLookupResult:
        try:
            hits = await self.provider.search_by_name(legal_name, _MAX_NAME_HITS)
        except Exception as exc:  # noqa: BLE001
            result.withheld.append(
                WithheldFinding(
                    scheme=SCHEME_LEI,
                    reason=WITHHELD_SOURCE_ERROR,
                    detail=f"GLEIF name search failed: {type(exc).__name__}: {exc}",
                )
            )
            return result

        hits = [h for h in (hits or []) if getattr(h, "lei", None)]
        names = tuple(str(getattr(h, "legal_name", "") or "") for h in hits)
        if not hits:
            result.withheld.append(
                WithheldFinding(
                    scheme=SCHEME_LEI,
                    reason=WITHHELD_NOT_FOUND,
                    detail=f"GLEIF returned no record whose legal name matches "
                    f"{legal_name!r}",
                )
            )
            return result

        wanted = normalize_entity_name(legal_name)
        exact = [h for h in hits if normalize_entity_name(
            getattr(h, "legal_name", "")
        ) == wanted]

        if len(hits) > 1 and len(exact) != 1:
            # THE case this whole slice's protocol amendment exists for. Several
            # records contain the name and nothing separates them; asserting one
            # would attribute a filing history on a substring.
            result.withheld.append(
                WithheldFinding(
                    scheme=SCHEME_LEI,
                    reason=WITHHELD_AMBIGUOUS_NAME_MATCH,
                    detail=(
                        f"{len(hits)} GLEIF records match {legal_name!r} as a "
                        "partial name and none is an unambiguous exact match"
                    ),
                    candidates=names,
                )
            )
            return result

        candidate = exact[0] if exact else hits[0]
        if not exact:
            # One hit, but it only CONTAINS the query. "Pandora" matching
            # "Pandora Media" is not the same company.
            result.withheld.append(
                WithheldFinding(
                    scheme=SCHEME_LEI,
                    reason=WITHHELD_PARTIAL_NAME_MATCH,
                    detail=(
                        f"the only GLEIF record for {legal_name!r} is "
                        f"{getattr(candidate, 'legal_name', None)!r}, which contains "
                        "the query rather than equalling it"
                    ),
                    candidates=names,
                )
            )
            return result

        result.claims.append(
            IdentifierClaim(
                scheme=SCHEME_LEI,
                value=str(getattr(candidate, "lei")),
                source=self.source_id,
                source_url=getattr(candidate, "source_url", None),
                confidence=_CONFIDENCE_EXACT_NAME,
                detail=(
                    "single GLEIF record whose legal name matches exactly: "
                    f"{getattr(candidate, 'legal_name', None)!r}"
                ),
            )
        )
        return result


@dataclass
class SecIdentifierSource:
    """CIK from SEC EDGAR.

    ``provider`` must expose ``resolve_cik(ticker, exchange)``;
    ``SecEdgarFundamentalsProvider`` does, and it already refuses a non-SEC-eligible
    venue **before any network call** and already prefers a verified
    ``sec_issuer_registry`` mapping over the ticker index.

    The verification gate in ``entities.claims`` re-checks the venue independently.
    That duplication is deliberate defence in depth: the provider checks the venue it
    was *asked about*, the gate checks the venues the entity *actually has*, and for
    a cross-listed issuer those are different questions.
    """

    provider: Any
    source_id: str = SEC_SOURCE_ID
    schemes: frozenset[str] = field(default_factory=lambda: frozenset({SCHEME_CIK}))

    async def lookup(self, query: IdentifierQuery) -> SourceLookupResult:
        result = SourceLookupResult(source_id=self.source_id)
        if not query.ticker:
            result.withheld.append(
                WithheldFinding(
                    scheme=SCHEME_CIK,
                    reason=WITHHELD_NOT_SUPPORTED,
                    detail=(
                        "SEC's index is keyed by ticker; a legal name alone is not "
                        "a lookup here"
                    ),
                )
            )
            return result

        try:
            cik = await self.provider.resolve_cik(query.ticker, query.exchange)
        except SecExchangeNotSupportedError:
            # The venue refusal, matched on the real exception CLASS. Matching on
            # its name would fail soft: rename the class and this branch stops
            # firing, the next one catches it as ValueError, and the venue rule
            # silently degrades into "not found" — losing exactly the distinction
            # the Boeing bug taught the platform to keep.
            result.withheld.append(
                WithheldFinding(
                    scheme=SCHEME_CIK,
                    reason=WITHHELD_VENUE_NOT_SEC_ELIGIBLE,
                    detail=(
                        f"SEC's ticker index does not cover "
                        f"{query.ticker!r} on {query.exchange!r}; a CIK derived "
                        "there returns an unrelated US issuer"
                    ),
                )
            )
            return result
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, ValueError):
                result.withheld.append(
                    WithheldFinding(
                        scheme=SCHEME_CIK,
                        reason=WITHHELD_NOT_FOUND,
                        detail=f"SEC has no CIK for {query.ticker!r}: {exc}",
                    )
                )
            else:
                result.withheld.append(
                    WithheldFinding(
                        scheme=SCHEME_CIK,
                        reason=WITHHELD_SOURCE_ERROR,
                        detail=f"SEC lookup failed: {type(exc).__name__}: {exc}",
                    )
                )
            return result

        if not cik:
            result.withheld.append(
                WithheldFinding(
                    scheme=SCHEME_CIK,
                    reason=WITHHELD_NOT_FOUND,
                    detail=f"SEC returned no CIK for {query.ticker!r}",
                )
            )
            return result

        result.claims.append(
            IdentifierClaim(
                scheme=SCHEME_CIK,
                value=str(cik),
                source=self.source_id,
                source_url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}",
                confidence=_CONFIDENCE_REGISTRY_CONFIRMED,
                detail=(
                    f"resolved from {query.ticker!r} on {query.exchange or 'US'} "
                    "through the SEC-eligible venue gate"
                ),
            )
        )
        return result


__all__ = [
    "GLEIF_SOURCE_ID",
    "SEC_SOURCE_ID",
    "GleifIdentifierSource",
    "SecIdentifierSource",
]
