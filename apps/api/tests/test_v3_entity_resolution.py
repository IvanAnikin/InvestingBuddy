"""Entity resolution and the identifier-claim gate — V3.2 Slice 2.3.

WHAT THESE TESTS PIN
====================
  * the four states, each reached by a distinct realistic input;
  * a name-only match is AMBIGUOUS, never resolved — even for exactly one
    candidate, because "this is the one" and "a second company of this name has
    not been ingested yet" are indistinguishable from here;
  * a LEI and a ticker pointing at two entities is CONFLICTING, not ambiguous:
    the inputs disagree and more evidence will not fix it;
  * `is_actionable` is true for exactly one state;
  * every rejection reason, including a CIK claimed for a listing on a venue SEC's
    ticker index does not cover — the Boeing bug as a rule rather than a special
    case;
  * a verified claim is recorded, is idempotent, and NEVER rewrites `entity_key`;
  * an identifier held by another subject is refused and the existing row is
    untouched;
  * with the flag off, resolution returns `unresolved` and writes nothing.

Real database, no clock dependence, and every LEI/ISIN generated from its own
check-digit rule rather than transcribed.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import company as _company  # noqa: F401
from app.models import legal_entity as _legal_entity  # noqa: F401
from app.models.legal_entity import (
    EntityIdentifier,
    LegalEntity,
    Security,
    SecurityListing,
)
from app.services.entities.claims import (
    REJECTED_HELD_BY_ANOTHER_ENTITY,
    REJECTED_INVALID_IDENTIFIER,
    REJECTED_NO_SOURCE,
    REJECTED_SUBJECT_MISMATCH,
    REJECTED_SUBJECT_NOT_FOUND,
    REJECTED_VENUE_NOT_SEC_ELIGIBLE,
    REJECTION_REASONS,
    IdentifierClaim,
    IdentifierQuery,
    IdentifierSource,
    StaticIdentifierSource,
    verify_identifier_claim,
)
from app.services.entities.identifiers import (
    SCHEME_CIK,
    SCHEME_ISIN,
    SCHEME_LEI,
    isin_check_digit,
    lei_check_digits,
)
from app.services.entities.master import (
    EntityInput,
    record_alias,
    record_identifier,
    upsert_legal_entity,
    upsert_listing,
    upsert_security,
)
from app.services.entities.resolution import (
    EVIDENCE_ALIAS,
    EVIDENCE_IDENTIFIER,
    EVIDENCE_LISTING,
    EVIDENCE_NAME,
    STRENGTH_DECISIVE,
    STRENGTH_STRONG,
    STRENGTH_WEAK,
    EntityQuery,
    resolve,
)
from app.services.entities.vocabulary import (
    ALIAS_FORMER_LEGAL_NAME,
    LISTING_ACTIVE,
    RESOLUTION_AMBIGUOUS,
    RESOLUTION_CONFLICTING,
    RESOLUTION_RESOLVED,
    RESOLUTION_UNRESOLVED,
)


def _lei(base18: str) -> str:
    return base18 + lei_check_digits(base18)


def _isin(prefix: str, nsin: str) -> str:
    body = f"{prefix}{nsin}"
    return body + str(isin_check_digit(body))


LEI_A = _lei("529900AAAAAAAAAA01")
LEI_B = _lei("213800BBBBBBBBBB02")
LEI_C = _lei("984500CCCCCCCCCC03")
ISIN_A = _isin("DK", "006025269")


def _cfg(**overrides: object) -> Settings:
    base: dict[str, object] = {"v3_entity_master_enabled": True}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _entity(
    session, cfg: Settings, *, key: str, name: str, **kw: object
) -> LegalEntity:
    entity = await upsert_legal_entity(
        session, EntityInput(entity_key=key, legal_name=name, **kw), cfg=cfg
    )
    assert entity is not None
    return entity


async def _listed(
    session, cfg: Settings, *, entity: LegalEntity, ticker: str, exchange: str
) -> Security:
    security = await upsert_security(
        session,
        legal_entity_id=entity.id,
        security_key="ordinary_share:1",
        cfg=cfg,
        is_primary=True,
    )
    assert security is not None
    await upsert_listing(
        session,
        security_id=security.id,
        ticker=ticker,
        exchange=exchange,
        cfg=cfg,
        listing_status=LISTING_ACTIVE,
        is_primary=True,
    )
    return security


# --------------------------------------------------------------------------- #
# The four states
# --------------------------------------------------------------------------- #


class TestResolvedState:
    async def test_a_listing_match_resolves(self, session) -> None:
        cfg = _cfg()
        entity = await _entity(
            session, cfg, key="listing:XCSE:PNDORA", name="Pandora A/S"
        )
        await _listed(session, cfg, entity=entity, ticker="PNDORA", exchange="CO")
        await session.commit()

        outcome = await resolve(
            session, EntityQuery(ticker="PNDORA", exchange="CO"), cfg=cfg
        )
        assert outcome.state == RESOLUTION_RESOLVED
        assert outcome.is_actionable is True
        assert outcome.entity is not None and outcome.entity.id == entity.id
        assert [e.kind for e in outcome.candidates[0].evidence] == [EVIDENCE_LISTING]
        assert outcome.candidates[0].best_strength == STRENGTH_STRONG

    async def test_an_identifier_match_resolves_and_is_decisive(self, session) -> None:
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="Pandora A/S")
        await record_identifier(
            session,
            scheme=SCHEME_LEI,
            value=LEI_A,
            cfg=cfg,
            legal_entity_id=entity.id,
            source="gleif",
        )
        await session.commit()

        outcome = await resolve(
            session, EntityQuery(identifiers={SCHEME_LEI: LEI_A}), cfg=cfg
        )
        assert outcome.state == RESOLUTION_RESOLVED
        assert outcome.candidates[0].best_strength == STRENGTH_DECISIVE
        assert outcome.candidates[0].evidence[0].kind == EVIDENCE_IDENTIFIER

    async def test_an_isin_resolves_through_its_security_to_the_entity(
        self, session
    ) -> None:
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="Pandora A/S")
        security = await _listed(
            session, cfg, entity=entity, ticker="PNDORA", exchange="CO"
        )
        await record_identifier(
            session,
            scheme=SCHEME_ISIN,
            value=ISIN_A,
            cfg=cfg,
            security_id=security.id,
            source="issuer_report",
        )
        await session.commit()

        outcome = await resolve(
            session, EntityQuery(identifiers={SCHEME_ISIN: ISIN_A}), cfg=cfg
        )
        assert outcome.state == RESOLUTION_RESOLVED
        assert outcome.entity is not None and outcome.entity.id == entity.id

    async def test_a_name_match_alongside_a_listing_still_resolves(
        self, session
    ) -> None:
        # Weak evidence does not spoil strong evidence; it accumulates on the same
        # candidate and the outcome records both reasons.
        cfg = _cfg()
        entity = await _entity(
            session, cfg, key="listing:XCSE:PNDORA", name="Pandora A/S"
        )
        await _listed(session, cfg, entity=entity, ticker="PNDORA", exchange="CO")
        await session.commit()

        outcome = await resolve(
            session,
            EntityQuery(ticker="PNDORA", exchange="CO", legal_name="Pandora A/S"),
            cfg=cfg,
        )
        assert outcome.state == RESOLUTION_RESOLVED
        kinds = {e.kind for e in outcome.candidates[0].evidence}
        assert kinds == {EVIDENCE_LISTING, EVIDENCE_NAME}


class TestDissentingCandidates:
    async def test_a_weak_match_on_a_different_entity_is_surfaced_not_dropped(
        self, session
    ) -> None:
        # Strong evidence beating weak evidence is correct. But a caller that
        # supplied a ticker AND a name, and finds the name matching a DIFFERENT
        # entity, has learned something real — that is the class of defect where a
        # report's legal_name came back as the ticker. The loser stays visible.
        cfg = _cfg()
        pandora = await _entity(
            session, cfg, key="listing:XCSE:PNDORA", name="Pandora A/S"
        )
        await _listed(session, cfg, entity=pandora, ticker="PNDORA", exchange="CO")
        await _entity(session, cfg, key="lei:" + LEI_B, name="Acme Holdings")
        await session.commit()

        outcome = await resolve(
            session,
            EntityQuery(ticker="PNDORA", exchange="CO", legal_name="Acme Holdings"),
            cfg=cfg,
        )
        assert outcome.state == RESOLUTION_RESOLVED
        assert outcome.entity is not None and outcome.entity.id == pandora.id
        dissenting = outcome.dissenting_candidates
        assert [c.entity.legal_name for c in dissenting] == ["Acme Holdings"]
        assert "weak evidence only" in outcome.reason
        assert "Acme Holdings" in outcome.reason

    async def test_with_no_winner_every_candidate_dissents(self, session) -> None:
        cfg = _cfg()
        await _entity(session, cfg, key="lei:" + LEI_A, name="Acme Holdings")
        await _entity(session, cfg, key="lei:" + LEI_B, name="Acme Holdings")
        await session.commit()
        outcome = await resolve(
            session, EntityQuery(legal_name="Acme Holdings"), cfg=cfg
        )
        assert outcome.state == RESOLUTION_AMBIGUOUS
        assert len(outcome.dissenting_candidates) == 2


class TestUnresolvedState:
    async def test_nothing_matched(self, session) -> None:
        cfg = _cfg()
        outcome = await resolve(
            session, EntityQuery(ticker="NOPE", exchange="CO"), cfg=cfg
        )
        assert outcome.state == RESOLUTION_UNRESOLVED
        assert outcome.is_actionable is False
        assert outcome.entity is None and outcome.candidates == []

    async def test_an_empty_query_is_unresolved_not_an_error(self, session) -> None:
        cfg = _cfg()
        outcome = await resolve(session, EntityQuery(), cfg=cfg)
        assert outcome.state == RESOLUTION_UNRESOLVED
        assert "names no ticker" in outcome.reason

    async def test_an_unreadable_identifier_is_reported_not_silently_ignored(
        self, session
    ) -> None:
        # "we could not read the LEI you gave us" is a different answer from
        # "we did not find it", and a caller needs to be able to tell them apart.
        cfg = _cfg()
        bad = LEI_A[:-1] + str((int(LEI_A[-1]) + 1) % 10)
        outcome = await resolve(
            session, EntityQuery(identifiers={SCHEME_LEI: bad}), cfg=cfg
        )
        assert outcome.state == RESOLUTION_UNRESOLVED
        assert [scheme for scheme, _ in outcome.invalid_inputs] == [SCHEME_LEI]
        assert "could not be read" in outcome.reason


class TestAmbiguousState:
    async def test_two_entities_sharing_a_name_are_ambiguous(self, session) -> None:
        cfg = _cfg()
        await _entity(
            session, cfg, key="lei:" + LEI_A, name="Acme Holdings", jurisdiction="DK"
        )
        await _entity(
            session, cfg, key="lei:" + LEI_B, name="Acme Holdings", jurisdiction="GB"
        )
        await session.commit()

        outcome = await resolve(
            session, EntityQuery(legal_name="Acme Holdings"), cfg=cfg
        )
        assert outcome.state == RESOLUTION_AMBIGUOUS
        assert outcome.is_actionable is False
        assert outcome.entity is None
        assert len(outcome.candidates) == 2

    async def test_a_single_name_only_match_is_still_ambiguous(self, session) -> None:
        # THE rule. One candidate on a name alone is not resolved, because a second
        # company of the same name may exist and simply not be ingested yet.
        cfg = _cfg()
        await _entity(session, cfg, key="lei:" + LEI_A, name="Pandora A/S")
        await session.commit()

        outcome = await resolve(session, EntityQuery(legal_name="Pandora A/S"), cfg=cfg)
        assert outcome.state == RESOLUTION_AMBIGUOUS
        assert outcome.is_actionable is False
        assert len(outcome.candidates) == 1, "the candidate is offered, not accepted"
        assert outcome.candidates[0].best_strength == STRENGTH_WEAK
        assert "never identity" in outcome.reason

    async def test_an_alias_match_alone_is_ambiguous_too(self, session) -> None:
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="Pandora A/S")
        await record_alias(
            session,
            legal_entity_id=entity.id,
            alias="Pandora Jewelry A/S",
            alias_type=ALIAS_FORMER_LEGAL_NAME,
            cfg=cfg,
            source="issuer_report",
        )
        await session.commit()

        outcome = await resolve(
            session, EntityQuery(legal_name="Pandora Jewelry A/S"), cfg=cfg
        )
        assert outcome.state == RESOLUTION_AMBIGUOUS
        assert outcome.candidates[0].evidence[0].kind == EVIDENCE_ALIAS

    async def test_a_jurisdiction_narrows_but_never_excludes_unknown(
        self, session
    ) -> None:
        # Every backfilled entity has jurisdiction NULL, meaning "not established".
        # Treating NULL as "not this one" would silently drop all of them.
        cfg = _cfg()
        await _entity(
            session, cfg, key="lei:" + LEI_A, name="Acme Holdings", jurisdiction="GB"
        )
        await _entity(session, cfg, key="listing:XCSE:ACME", name="Acme Holdings")
        await session.commit()

        outcome = await resolve(
            session, EntityQuery(legal_name="Acme Holdings", jurisdiction="DK"), cfg=cfg
        )
        # The GB entity is excluded; the jurisdiction-unknown one is not.
        assert outcome.state == RESOLUTION_AMBIGUOUS
        assert len(outcome.candidates) == 1
        assert outcome.candidates[0].entity.jurisdiction is None


class TestConflictingState:
    async def test_a_lei_and_a_ticker_pointing_at_two_entities_conflict(
        self, session
    ) -> None:
        # The inputs actively disagree. More evidence will not fix this, which is
        # why it is not `ambiguous`.
        cfg = _cfg()
        by_lei = await _entity(session, cfg, key="lei:" + LEI_A, name="First A/S")
        await record_identifier(
            session,
            scheme=SCHEME_LEI,
            value=LEI_A,
            cfg=cfg,
            legal_entity_id=by_lei.id,
            source="gleif",
        )
        by_ticker = await _entity(
            session, cfg, key="listing:XCSE:PNDORA", name="Second A/S"
        )
        await _listed(session, cfg, entity=by_ticker, ticker="PNDORA", exchange="CO")
        await session.commit()

        outcome = await resolve(
            session,
            EntityQuery(
                ticker="PNDORA", exchange="CO", identifiers={SCHEME_LEI: LEI_A}
            ),
            cfg=cfg,
        )
        assert outcome.state == RESOLUTION_CONFLICTING
        assert outcome.is_actionable is False
        assert outcome.entity is None
        assert set(outcome.candidate_ids) == {by_lei.id, by_ticker.id}
        assert "disagree" in outcome.reason
        assert "First A/S" in outcome.reason and "Second A/S" in outcome.reason

    async def test_two_identifiers_pointing_at_two_entities_conflict(
        self, session
    ) -> None:
        cfg = _cfg()
        first = await _entity(session, cfg, key="lei:" + LEI_A, name="First A/S")
        second = await _entity(session, cfg, key="lei:" + LEI_B, name="Second A/S")
        await record_identifier(
            session,
            scheme=SCHEME_LEI,
            value=LEI_A,
            cfg=cfg,
            legal_entity_id=first.id,
            source="gleif",
        )
        await record_identifier(
            session,
            scheme=SCHEME_CIK,
            value="320193",
            cfg=cfg,
            legal_entity_id=second.id,
            source="sec_edgar",
        )
        await session.commit()

        outcome = await resolve(
            session,
            EntityQuery(identifiers={SCHEME_LEI: LEI_A, SCHEME_CIK: "320193"}),
            cfg=cfg,
        )
        assert outcome.state == RESOLUTION_CONFLICTING

    async def test_conflicting_is_not_collapsed_into_ambiguous(self, session) -> None:
        # Two states, two meanings. Collapsing them would hide "something upstream
        # is wrong" inside the noise of "we need more evidence".
        assert RESOLUTION_CONFLICTING != RESOLUTION_AMBIGUOUS


class TestOnlyOneStateIsActionable:
    async def test_the_actionable_check_is_a_single_property(self, session) -> None:
        cfg = _cfg()
        entity = await _entity(
            session, cfg, key="listing:XCSE:PNDORA", name="Pandora A/S"
        )
        await _listed(session, cfg, entity=entity, ticker="PNDORA", exchange="CO")
        await _entity(session, cfg, key="lei:" + LEI_B, name="Acme Holdings")
        await _entity(session, cfg, key="lei:" + LEI_C, name="Acme Holdings")
        await session.commit()

        resolved = await resolve(
            session, EntityQuery(ticker="PNDORA", exchange="CO"), cfg=cfg
        )
        ambiguous = await resolve(
            session, EntityQuery(legal_name="Acme Holdings"), cfg=cfg
        )
        unresolved = await resolve(session, EntityQuery(ticker="NOPE"), cfg=cfg)

        assert resolved.is_actionable is True
        assert ambiguous.is_actionable is False
        assert unresolved.is_actionable is False


# --------------------------------------------------------------------------- #
# The claim gate
# --------------------------------------------------------------------------- #


class TestClaimVerification:
    async def test_a_valid_claim_is_recorded(self, session) -> None:
        cfg = _cfg()
        entity = await _entity(
            session, cfg, key="listing:XCSE:PNDORA", name="Pandora A/S"
        )
        outcome = await verify_identifier_claim(
            session,
            claim=IdentifierClaim(
                scheme=SCHEME_LEI,
                value=LEI_A,
                source="gleif",
                source_url="https://api.gleif.org/api/v1/lei-records/" + LEI_A,
                confidence=1.0,
                effective_from=date(2026, 1, 1),
            ),
            cfg=cfg,
            legal_entity_id=entity.id,
        )
        await session.commit()
        assert outcome.accepted is True
        assert outcome.rejection_reason is None
        assert outcome.identifier_id is not None

        row = (await session.execute(select(EntityIdentifier))).scalar_one()
        assert row.value_normalized == LEI_A
        assert row.checksum_verified is True
        assert row.source == "gleif"
        assert row.effective_from == date(2026, 1, 1)

    async def test_promotion_never_rewrites_the_entity_key(self, session) -> None:
        # An entity created from a ticker keeps `listing:...` forever. Rewriting the
        # key would be an implicit merge of the identity's history and would break
        # every key a caller had recorded.
        cfg = _cfg()
        entity = await _entity(
            session, cfg, key="listing:XCSE:PNDORA", name="Pandora A/S"
        )
        original_key = entity.entity_key
        await verify_identifier_claim(
            session,
            claim=IdentifierClaim(scheme=SCHEME_LEI, value=LEI_A, source="gleif"),
            cfg=cfg,
            legal_entity_id=entity.id,
        )
        await session.commit()
        await session.refresh(entity)
        assert entity.entity_key == original_key == "listing:XCSE:PNDORA"
        # And the entity is now findable by the stronger identifier too.
        outcome = await resolve(
            session, EntityQuery(identifiers={SCHEME_LEI: LEI_A}), cfg=cfg
        )
        assert outcome.state == RESOLUTION_RESOLVED
        assert outcome.entity is not None and outcome.entity.id == entity.id

    async def test_the_same_claim_twice_is_idempotent(self, session) -> None:
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="Pandora A/S")
        claim = IdentifierClaim(scheme=SCHEME_LEI, value=LEI_A, source="gleif")
        first = await verify_identifier_claim(
            session, claim=claim, cfg=cfg, legal_entity_id=entity.id
        )
        second = await verify_identifier_claim(
            session, claim=claim, cfg=cfg, legal_entity_id=entity.id
        )
        await session.commit()
        assert first.accepted and second.accepted
        assert first.identifier_id == second.identifier_id
        count = (
            await session.execute(select(func.count()).select_from(EntityIdentifier))
        ).scalar_one()
        assert count == 1


class TestClaimRejections:
    async def test_a_checksum_invalid_claim_is_refused_with_a_reason(
        self, session
    ) -> None:
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="Pandora A/S")
        bad = LEI_A[:-1] + str((int(LEI_A[-1]) + 1) % 10)
        outcome = await verify_identifier_claim(
            session,
            claim=IdentifierClaim(scheme=SCHEME_LEI, value=bad, source="aggregator"),
            cfg=cfg,
            legal_entity_id=entity.id,
        )
        await session.commit()
        assert outcome.rejected is True
        assert outcome.rejection_reason == REJECTED_INVALID_IDENTIFIER
        assert "MOD 97-10" in (outcome.detail or "")
        count = (
            await session.execute(select(func.count()).select_from(EntityIdentifier))
        ).scalar_one()
        assert count == 0

    async def test_an_identifier_held_by_another_entity_is_refused_not_moved(
        self, session
    ) -> None:
        cfg = _cfg()
        holder = await _entity(session, cfg, key="lei:" + LEI_A, name="First A/S")
        other = await _entity(session, cfg, key="lei:" + LEI_B, name="Second A/S")
        await record_identifier(
            session,
            scheme=SCHEME_LEI,
            value=LEI_A,
            cfg=cfg,
            legal_entity_id=holder.id,
            source="gleif",
        )
        await session.commit()

        outcome = await verify_identifier_claim(
            session,
            claim=IdentifierClaim(scheme=SCHEME_LEI, value=LEI_A, source="aggregator"),
            cfg=cfg,
            legal_entity_id=other.id,
        )
        await session.commit()
        assert outcome.rejection_reason == REJECTED_HELD_BY_ANOTHER_ENTITY
        row = (await session.execute(select(EntityIdentifier))).scalar_one()
        assert row.legal_entity_id == holder.id, "the existing row is untouched"
        assert row.source == "gleif"

    async def test_a_cik_claimed_for_a_non_sec_eligible_venue_is_refused(
        self, session
    ) -> None:
        # THE BOEING BUG, AS A RULE. SEC's ticker index covers US registrants by
        # ticker string alone, so BA + LSE returns Boeing's CIK for BAE Systems.
        cfg = _cfg()
        bae = await _entity(session, cfg, key="listing:XLON:BA", name="BAE Systems plc")
        await _listed(session, cfg, entity=bae, ticker="BA", exchange="LSE")
        await session.commit()

        outcome = await verify_identifier_claim(
            session,
            claim=IdentifierClaim(
                scheme=SCHEME_CIK, value="12927", source="sec_ticker_index"
            ),
            cfg=cfg,
            legal_entity_id=bae.id,
        )
        await session.commit()
        assert outcome.rejection_reason == REJECTED_VENUE_NOT_SEC_ELIGIBLE
        assert "unrelated US issuer" in (outcome.detail or "")
        count = (
            await session.execute(select(func.count()).select_from(EntityIdentifier))
        ).scalar_one()
        assert count == 0

    async def test_a_cik_is_accepted_for_a_us_listed_entity(self, session) -> None:
        cfg = _cfg()
        boeing = await _entity(
            session, cfg, key="listing:XNYS:BA", name="The Boeing Company"
        )
        await _listed(session, cfg, entity=boeing, ticker="BA", exchange="NYSE")
        await session.commit()

        outcome = await verify_identifier_claim(
            session,
            claim=IdentifierClaim(
                scheme=SCHEME_CIK, value="12927", source="sec_edgar"
            ),
            cfg=cfg,
            legal_entity_id=boeing.id,
        )
        await session.commit()
        assert outcome.accepted is True

    async def test_a_cik_is_accepted_when_the_entity_has_any_us_listing(
        self, session
    ) -> None:
        # A cross-listed issuer with an ADR on a US venue is genuinely a filer. The
        # rule blocks a derived CIK, not a real one.
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="An Issuer S.A.")
        ordinary = await upsert_security(
            session,
            legal_entity_id=entity.id,
            security_key="ordinary_share:1",
            cfg=cfg,
            is_primary=True,
        )
        adr = await upsert_security(
            session, legal_entity_id=entity.id, security_key="adr:1", cfg=cfg
        )
        assert ordinary is not None and adr is not None
        await upsert_listing(
            session,
            security_id=ordinary.id,
            ticker="ISS",
            exchange="PA",
            cfg=cfg,
            listing_status=LISTING_ACTIVE,
        )
        await upsert_listing(
            session,
            security_id=adr.id,
            ticker="ISSAY",
            exchange="NYSE",
            cfg=cfg,
            listing_status=LISTING_ACTIVE,
        )
        await session.commit()

        outcome = await verify_identifier_claim(
            session,
            claim=IdentifierClaim(
                scheme=SCHEME_CIK, value="1234567", source="sec_edgar"
            ),
            cfg=cfg,
            legal_entity_id=entity.id,
        )
        await session.commit()
        assert outcome.accepted is True

    async def test_a_listing_with_no_exchange_code_counts_as_not_sec_eligible(
        self, session
    ) -> None:
        # A venue the platform cannot even name is certainly not one SEC's ticker
        # index covers. Dropping it from the set would let the rule pass on the
        # weakest possible input, which is the wrong direction to fail.
        cfg = _cfg()
        entity = await _entity(session, cfg, key="listing:UNKNOWN:XY", name="An Issuer")
        security = await upsert_security(
            session,
            legal_entity_id=entity.id,
            security_key="ordinary_share:1",
            cfg=cfg,
            is_primary=True,
        )
        assert security is not None
        session.add(
            SecurityListing(
                id=uuid.uuid4(),
                security_id=security.id,
                venue_key="UNKNOWN",
                exchange_code=None,
                ticker="XY",
                listing_status=LISTING_ACTIVE,
            )
        )
        await session.commit()

        outcome = await verify_identifier_claim(
            session,
            claim=IdentifierClaim(
                scheme=SCHEME_CIK, value="12927", source="a_us_ticker_index"
            ),
            cfg=cfg,
            legal_entity_id=entity.id,
        )
        await session.commit()
        assert outcome.rejection_reason == REJECTED_VENUE_NOT_SEC_ELIGIBLE
        assert "unknown" in (outcome.detail or "")

    async def test_a_cik_for_an_entity_with_no_listing_is_accepted_deliberately(
        self, session
    ) -> None:
        # "We cannot evaluate the venue" is not "the venue is disqualified".
        # Refusing every CIK for a listing-less entity would block the legitimate
        # case of an issuer known only by its LEI, so the rule narrows on purpose
        # and this test makes that a decision rather than an oversight.
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="An Issuer S.A.")
        await session.commit()
        outcome = await verify_identifier_claim(
            session,
            claim=IdentifierClaim(
                scheme=SCHEME_CIK, value="1234567", source="sec_edgar"
            ),
            cfg=cfg,
            legal_entity_id=entity.id,
        )
        await session.commit()
        assert outcome.accepted is True

    async def test_an_acceptance_says_what_was_actually_checked(
        self, session
    ) -> None:
        # `checksum_verified` False is not a weaker acceptance — it means the scheme
        # has no check digits. A reader needs the difference stated, not inferred.
        cfg = _cfg()
        entity = await _entity(
            session, cfg, key="listing:XNYS:BA", name="The Boeing Company"
        )
        await _listed(session, cfg, entity=entity, ticker="BA", exchange="NYSE")
        await session.commit()

        with_checksum = await verify_identifier_claim(
            session,
            claim=IdentifierClaim(scheme=SCHEME_LEI, value=LEI_A, source="gleif"),
            cfg=cfg,
            legal_entity_id=entity.id,
        )
        without = await verify_identifier_claim(
            session,
            claim=IdentifierClaim(
                scheme=SCHEME_CIK, value="12927", source="sec_edgar"
            ),
            cfg=cfg,
            legal_entity_id=entity.id,
        )
        await session.commit()
        assert with_checksum.accepted and without.accepted
        assert "checksum verified" in (with_checksum.detail or "")
        assert "no verified check digit" in (without.detail or "")

    async def test_an_isin_claimed_for_a_legal_entity_is_a_subject_mismatch(
        self, session
    ) -> None:
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="Pandora A/S")
        outcome = await verify_identifier_claim(
            session,
            claim=IdentifierClaim(
                scheme=SCHEME_ISIN, value=ISIN_A, source="issuer_report"
            ),
            cfg=cfg,
            legal_entity_id=entity.id,
        )
        assert outcome.rejection_reason == REJECTED_SUBJECT_MISMATCH

    async def test_a_lei_claimed_for_a_security_is_a_subject_mismatch(
        self, session
    ) -> None:
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="Pandora A/S")
        security = await _listed(
            session, cfg, entity=entity, ticker="PNDORA", exchange="CO"
        )
        await session.commit()
        outcome = await verify_identifier_claim(
            session,
            claim=IdentifierClaim(scheme=SCHEME_LEI, value=LEI_A, source="gleif"),
            cfg=cfg,
            security_id=security.id,
        )
        assert outcome.rejection_reason == REJECTED_SUBJECT_MISMATCH

    async def test_an_unsourced_claim_is_refused(self, session) -> None:
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="Pandora A/S")
        outcome = await verify_identifier_claim(
            session,
            claim=IdentifierClaim(scheme=SCHEME_LEI, value=LEI_A, source="  "),
            cfg=cfg,
            legal_entity_id=entity.id,
        )
        assert outcome.rejection_reason == REJECTED_NO_SOURCE

    async def test_a_claim_for_a_nonexistent_entity_is_refused(self, session) -> None:
        cfg = _cfg()
        outcome = await verify_identifier_claim(
            session,
            claim=IdentifierClaim(scheme=SCHEME_LEI, value=LEI_A, source="gleif"),
            cfg=cfg,
            legal_entity_id=uuid.uuid4(),
        )
        assert outcome.rejection_reason == REJECTED_SUBJECT_NOT_FOUND

    async def test_every_rejection_reason_is_in_the_declared_vocabulary(self) -> None:
        # A reason invented at a call site is a reason nothing can aggregate on,
        # and per-source accuracy is the whole point of keeping rejections.
        for reason in (
            REJECTED_INVALID_IDENTIFIER,
            REJECTED_HELD_BY_ANOTHER_ENTITY,
            REJECTED_VENUE_NOT_SEC_ELIGIBLE,
            REJECTED_SUBJECT_MISMATCH,
            REJECTED_NO_SOURCE,
            REJECTED_SUBJECT_NOT_FOUND,
        ):
            assert reason in REJECTION_REASONS


# --------------------------------------------------------------------------- #
# The source contract
# --------------------------------------------------------------------------- #


class TestIdentifierSourceContract:
    async def test_the_static_source_satisfies_the_protocol(self) -> None:
        source = StaticIdentifierSource(
            source_id="fixture", schemes=frozenset({SCHEME_LEI})
        )
        assert isinstance(source, IdentifierSource)

    async def test_a_source_only_returns_claims_in_its_declared_schemes(self) -> None:
        # A source that answers outside its declared schemes is a source whose
        # governance and rate limits were reasoned about for the wrong thing.
        source = StaticIdentifierSource(
            source_id="fixture",
            schemes=frozenset({SCHEME_LEI}),
            claims_by_ticker={
                "PNDORA": [
                    IdentifierClaim(scheme=SCHEME_LEI, value=LEI_A, source="fixture"),
                    IdentifierClaim(scheme=SCHEME_CIK, value="1", source="fixture"),
                ]
            },
        )
        result = await source.lookup(IdentifierQuery(ticker="pndora"))
        assert [c.scheme for c in result.claims] == [SCHEME_LEI]
        assert result.source_id == "fixture"

    async def test_claims_from_a_source_still_go_through_the_gate(
        self, session
    ) -> None:
        # A source is not trusted more than a caller. This is the promotion path:
        # a claim, then verification, then a canonical row.
        cfg = _cfg()
        bae = await _entity(session, cfg, key="listing:XLON:BA", name="BAE Systems plc")
        await _listed(session, cfg, entity=bae, ticker="BA", exchange="LSE")
        await session.commit()

        source = StaticIdentifierSource(
            source_id="a_us_ticker_index",
            schemes=frozenset({SCHEME_CIK}),
            claims_by_ticker={
                "BA": [
                    IdentifierClaim(
                        scheme=SCHEME_CIK, value="12927", source="a_us_ticker_index"
                    )
                ]
            },
        )
        result = await source.lookup(IdentifierQuery(ticker="BA", exchange="LSE"))
        outcomes = [
            await verify_identifier_claim(
                session, claim=claim, cfg=cfg, legal_entity_id=bae.id
            )
            for claim in result.claims
        ]
        await session.commit()
        assert [o.rejection_reason for o in outcomes] == [
            REJECTED_VENUE_NOT_SEC_ELIGIBLE
        ]


class TestDisabledByDefault:
    async def test_with_the_flag_off_resolution_is_unresolved(self, session) -> None:
        off = _cfg(v3_entity_master_enabled=False)
        outcome = await resolve(
            session, EntityQuery(ticker="PNDORA", exchange="CO"), cfg=off
        )
        assert outcome.state == RESOLUTION_UNRESOLVED
        assert "disabled" in outcome.reason

    async def test_with_the_flag_off_no_claim_is_recorded(self, session) -> None:
        off = _cfg(v3_entity_master_enabled=False)
        outcome = await verify_identifier_claim(
            session,
            claim=IdentifierClaim(scheme=SCHEME_LEI, value=LEI_A, source="gleif"),
            cfg=off,
            legal_entity_id=uuid.uuid4(),
        )
        await session.commit()
        assert outcome.rejected is True
        count = (
            await session.execute(select(func.count()).select_from(EntityIdentifier))
        ).scalar_one()
        assert count == 0
