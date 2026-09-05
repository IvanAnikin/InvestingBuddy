"""Live identifier sources and promotion — V3.2 Slice 2.3.1.

WHAT THESE TESTS PIN
====================
The rule the protocol amendment exists for: **withhold rather than assert**.

GLEIF's name filter is a PARTIAL match, so "Pandora" returns every legal name
containing it. Emitting those as claims would let the verification gate accept
whichever LEI happens to be unheld — the gate can detect a LEI already held by
another subject and cannot detect one belonging to a company nobody has ingested.
That is a silent misattribution of an entire filing history.

So:
  * several partial hits  -> ZERO claims and `ambiguous_name_match`;
  * one hit that only CONTAINS the query -> zero claims and `partial_name_match`;
  * one exact hit -> one claim, confidence 0.9, NOT 1.0 (a name is still a name);
  * a confirmed LEI -> one claim, confidence 1.0;
  * a ticker-only GLEIF query -> zero claims, `not_supported`;
  * an LSE ticker at SEC -> zero claims, `venue_not_sec_eligible`, and the provider
    is NOT called past its own gate;
  * a source that raises is contained and the others still run;
  * promotion records an identifier and never rewrites `entity_key`.

Every provider is a FAKE. No test here touches the network — a live contract test
would only make these proofs depend on a registry's uptime, and both registries are
free so there is no spend to justify either way.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.integrations.entity_identifier_sources import (
    GLEIF_SOURCE_ID,
    SEC_SOURCE_ID,
    GleifIdentifierSource,
    SecIdentifierSource,
)
from app.integrations.providers.sec_edgar_fundamentals import (
    SecExchangeNotSupportedError,
)
from app.models import company as _company  # noqa: F401
from app.models import legal_entity as _legal_entity  # noqa: F401
from app.models.legal_entity import EntityIdentifier, LegalEntity
from app.services.entities.claims import (
    REJECTED_VENUE_NOT_SEC_ELIGIBLE,
    WITHHELD_AMBIGUOUS_NAME_MATCH,
    WITHHELD_NOT_FOUND,
    WITHHELD_NOT_SUPPORTED,
    WITHHELD_PARTIAL_NAME_MATCH,
    WITHHELD_SOURCE_ERROR,
    WITHHELD_VENUE_NOT_SEC_ELIGIBLE,
    IdentifierClaim,
    IdentifierQuery,
    IdentifierSource,
    StaticIdentifierSource,
)
from app.services.entities.identifiers import (
    SCHEME_CIK,
    SCHEME_LEI,
    lei_check_digits,
)
from app.services.entities.master import (
    EntityInput,
    upsert_legal_entity,
    upsert_listing,
    upsert_security,
)
from app.services.entities.promotion import promote_entity_identity
from app.services.entities.vocabulary import LISTING_ACTIVE


def _lei(base18: str) -> str:
    return base18 + lei_check_digits(base18)


LEI_A = _lei("529900AAAAAAAAAA01")
LEI_B = _lei("213800BBBBBBBBBB02")


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


# --------------------------------------------------------------------------- #
# Fakes — no network, and they record what they were asked
# --------------------------------------------------------------------------- #


@dataclass
class FakeGleifRecord:
    """The shape ``GleifProvider`` returns: a ``CompanyProfileData``-alike."""

    lei: str
    legal_name: str
    source_url: str | None = None


@dataclass
class FakeGleifProvider:
    by_lei: dict[str, FakeGleifRecord] = field(default_factory=dict)
    by_name: dict[str, list[FakeGleifRecord]] = field(default_factory=dict)
    raise_on_lei: Exception | None = None
    raise_on_search: Exception | None = None
    calls: list[str] = field(default_factory=list)

    async def get_by_lei(self, lei: str) -> FakeGleifRecord:
        self.calls.append(f"get_by_lei:{lei}")
        if self.raise_on_lei is not None:
            raise self.raise_on_lei
        record = self.by_lei.get(lei.upper())
        if record is None:
            raise ValueError(f"LEI '{lei.upper()}' not found in GLEIF registry.")
        return record

    async def search_by_name(
        self, company_name: str, page_size: int = 5
    ) -> list[FakeGleifRecord]:
        self.calls.append(f"search_by_name:{company_name}")
        if self.raise_on_search is not None:
            raise self.raise_on_search
        return self.by_name.get(company_name.strip().lower(), [])[:page_size]


@dataclass
class FakeSecProvider:
    ciks: dict[tuple[str, str | None], str] = field(default_factory=dict)
    unsupported: set[tuple[str, str | None]] = field(default_factory=set)
    raise_other: Exception | None = None
    calls: list[tuple[str, str | None]] = field(default_factory=list)

    async def resolve_cik(self, ticker: str, exchange: str | None = None) -> str:
        key = (ticker.upper(), exchange)
        self.calls.append(key)
        if key in self.unsupported:
            # The REAL exception class, so the adapter's `except` clause is the
            # thing under test rather than a string comparison that agrees with it.
            raise SecExchangeNotSupportedError(ticker, exchange)
        if self.raise_other is not None:
            raise self.raise_other
        cik = self.ciks.get(key)
        if cik is None:
            raise ValueError(f"Ticker '{ticker}' not found in SEC index.")
        return cik


# --------------------------------------------------------------------------- #
# GLEIF
# --------------------------------------------------------------------------- #


class TestGleifSource:
    async def test_it_satisfies_the_protocol(self) -> None:
        source = GleifIdentifierSource(provider=FakeGleifProvider())
        assert isinstance(source, IdentifierSource)
        assert source.source_id == GLEIF_SOURCE_ID
        assert source.schemes == frozenset({SCHEME_LEI})

    async def test_a_known_lei_is_confirmed_at_full_confidence(self) -> None:
        provider = FakeGleifProvider(
            by_lei={
                LEI_A: FakeGleifRecord(
                    lei=LEI_A,
                    legal_name="Pandora A/S",
                    source_url="https://api.gleif.org/api/v1/lei-records/" + LEI_A,
                )
            }
        )
        source = GleifIdentifierSource(provider=provider)
        result = await source.lookup(
            IdentifierQuery(known_identifiers={SCHEME_LEI: LEI_A})
        )
        assert result.withheld == []
        assert len(result.claims) == 1
        claim = result.claims[0]
        assert claim.scheme == SCHEME_LEI and claim.value == LEI_A
        assert claim.confidence == 1.0, "a registry confirming its own identifier"
        assert claim.source == GLEIF_SOURCE_ID
        assert "Pandora A/S" in (claim.detail or "")

    async def test_an_unknown_lei_is_not_found_not_a_claim(self) -> None:
        source = GleifIdentifierSource(provider=FakeGleifProvider())
        result = await source.lookup(
            IdentifierQuery(known_identifiers={SCHEME_LEI: LEI_A})
        )
        assert result.claims == []
        assert [w.reason for w in result.withheld] == [WITHHELD_NOT_FOUND]

    async def test_one_exact_name_match_yields_a_claim_below_full_confidence(
        self,
    ) -> None:
        # 0.9, not 1.0. An exact string match on a name is still a name, and two
        # entities may legally share one — the resolver treats a name as weak
        # evidence for the same reason.
        provider = FakeGleifProvider(
            by_name={"pandora a/s": [FakeGleifRecord(lei=LEI_A, legal_name="Pandora A/S")]}
        )
        source = GleifIdentifierSource(provider=provider)
        result = await source.lookup(IdentifierQuery(legal_name="Pandora A/S"))
        assert result.withheld == []
        assert len(result.claims) == 1
        assert result.claims[0].confidence == 0.9
        assert result.claims[0].confidence < 1.0

    async def test_several_partial_hits_yield_no_claim_and_a_stated_reason(
        self,
    ) -> None:
        # THE case the protocol amendment exists for.
        provider = FakeGleifProvider(
            by_name={
                "pandora": [
                    FakeGleifRecord(lei=LEI_A, legal_name="Pandora A/S"),
                    FakeGleifRecord(lei=LEI_B, legal_name="Pandora Media, Inc."),
                ]
            }
        )
        source = GleifIdentifierSource(provider=provider)
        result = await source.lookup(IdentifierQuery(legal_name="Pandora"))
        assert result.claims == [], (
            "asserting one of several partial matches would attribute a filing "
            "history on a substring"
        )
        assert [w.reason for w in result.withheld] == [WITHHELD_AMBIGUOUS_NAME_MATCH]
        assert set(result.withheld[0].candidates) == {
            "Pandora A/S",
            "Pandora Media, Inc.",
        }
        assert "2 GLEIF records" in result.withheld[0].detail

    async def test_a_single_partial_hit_is_withheld_too(self) -> None:
        # "Pandora" matching only "Pandora Media" is not the same company, and one
        # hit does not make it one.
        provider = FakeGleifProvider(
            by_name={
                "pandora": [FakeGleifRecord(lei=LEI_B, legal_name="Pandora Media, Inc.")]
            }
        )
        source = GleifIdentifierSource(provider=provider)
        result = await source.lookup(IdentifierQuery(legal_name="Pandora"))
        assert result.claims == []
        assert [w.reason for w in result.withheld] == [WITHHELD_PARTIAL_NAME_MATCH]
        assert "Pandora Media, Inc." in result.withheld[0].detail

    async def test_several_hits_with_one_exact_match_resolve_to_that_one(self) -> None:
        # A partial filter returning extra rows does not make an exact match
        # ambiguous. This is the common real shape and it must still work.
        provider = FakeGleifProvider(
            by_name={
                "pandora a/s": [
                    FakeGleifRecord(lei=LEI_B, legal_name="Pandora A/S Holdings"),
                    FakeGleifRecord(lei=LEI_A, legal_name="Pandora A/S"),
                ]
            }
        )
        source = GleifIdentifierSource(provider=provider)
        result = await source.lookup(IdentifierQuery(legal_name="Pandora A/S"))
        assert [c.value for c in result.claims] == [LEI_A]
        assert result.withheld == []

    async def test_two_exact_matches_are_ambiguous_not_a_coin_flip(self) -> None:
        provider = FakeGleifProvider(
            by_name={
                "acme holdings": [
                    FakeGleifRecord(lei=LEI_A, legal_name="Acme Holdings"),
                    FakeGleifRecord(lei=LEI_B, legal_name="Acme Holdings"),
                ]
            }
        )
        source = GleifIdentifierSource(provider=provider)
        result = await source.lookup(IdentifierQuery(legal_name="Acme Holdings"))
        assert result.claims == []
        assert [w.reason for w in result.withheld] == [WITHHELD_AMBIGUOUS_NAME_MATCH]

    async def test_a_ticker_only_query_is_not_supported(self) -> None:
        # GLEIF has no ticker index. Deriving a name from a ticker so this could
        # answer is how `BA` becomes Boeing.
        provider = FakeGleifProvider()
        source = GleifIdentifierSource(provider=provider)
        result = await source.lookup(IdentifierQuery(ticker="PNDORA", exchange="CO"))
        assert result.claims == []
        assert [w.reason for w in result.withheld] == [WITHHELD_NOT_SUPPORTED]
        assert provider.calls == [], "and it does not call the provider to find out"

    async def test_no_matches_is_not_found(self) -> None:
        source = GleifIdentifierSource(provider=FakeGleifProvider())
        result = await source.lookup(IdentifierQuery(legal_name="Nobody A/S"))
        assert result.claims == []
        assert [w.reason for w in result.withheld] == [WITHHELD_NOT_FOUND]

    async def test_a_provider_failure_is_recorded_not_turned_into_no_result(
        self,
    ) -> None:
        # "The registry timed out" and "the registry has no such entity" are
        # different answers and must not read alike.
        provider = FakeGleifProvider(raise_on_search=RuntimeError("connect timeout"))
        source = GleifIdentifierSource(provider=provider)
        result = await source.lookup(IdentifierQuery(legal_name="Pandora A/S"))
        assert result.claims == []
        assert [w.reason for w in result.withheld] == [WITHHELD_SOURCE_ERROR]
        assert "connect timeout" in result.withheld[0].detail


# --------------------------------------------------------------------------- #
# SEC
# --------------------------------------------------------------------------- #


class TestSecSource:
    async def test_it_satisfies_the_protocol(self) -> None:
        source = SecIdentifierSource(provider=FakeSecProvider())
        assert isinstance(source, IdentifierSource)
        assert source.source_id == SEC_SOURCE_ID
        assert source.schemes == frozenset({SCHEME_CIK})

    async def test_a_us_ticker_yields_a_cik_claim(self) -> None:
        provider = FakeSecProvider(ciks={("BA", "NYSE"): "0000012927"})
        source = SecIdentifierSource(provider=provider)
        result = await source.lookup(IdentifierQuery(ticker="BA", exchange="NYSE"))
        assert result.withheld == []
        assert [c.value for c in result.claims] == ["0000012927"]
        assert result.claims[0].confidence == 1.0
        assert "sec.gov" in (result.claims[0].source_url or "")

    async def test_a_non_sec_eligible_venue_is_withheld(self) -> None:
        provider = FakeSecProvider(unsupported={("BA", "LSE")})
        source = SecIdentifierSource(provider=provider)
        result = await source.lookup(IdentifierQuery(ticker="BA", exchange="LSE"))
        assert result.claims == []
        assert [w.reason for w in result.withheld] == [
            WITHHELD_VENUE_NOT_SEC_ELIGIBLE
        ]
        assert "unrelated US issuer" in result.withheld[0].detail

    async def test_an_unknown_ticker_is_not_found_not_an_error(self) -> None:
        source = SecIdentifierSource(provider=FakeSecProvider())
        result = await source.lookup(IdentifierQuery(ticker="NOPE", exchange="NYSE"))
        assert [w.reason for w in result.withheld] == [WITHHELD_NOT_FOUND]

    async def test_a_transport_failure_is_a_source_error(self) -> None:
        provider = FakeSecProvider(raise_other=RuntimeError("503"))
        source = SecIdentifierSource(provider=provider)
        result = await source.lookup(IdentifierQuery(ticker="BA", exchange="NYSE"))
        assert [w.reason for w in result.withheld] == [WITHHELD_SOURCE_ERROR]

    async def test_a_name_only_query_is_not_supported_and_calls_nothing(self) -> None:
        provider = FakeSecProvider()
        source = SecIdentifierSource(provider=provider)
        result = await source.lookup(IdentifierQuery(legal_name="Pandora A/S"))
        assert [w.reason for w in result.withheld] == [WITHHELD_NOT_SUPPORTED]
        assert provider.calls == []


# --------------------------------------------------------------------------- #
# Promotion
# --------------------------------------------------------------------------- #


async def _entity(session, cfg: Settings, *, key: str, name: str) -> LegalEntity:
    entity = await upsert_legal_entity(
        session, EntityInput(entity_key=key, legal_name=name), cfg=cfg
    )
    assert entity is not None
    return entity


async def _listed(
    session, cfg: Settings, *, entity: LegalEntity, ticker: str, exchange: str
) -> None:
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


class TestPromotion:
    async def test_a_listing_keyed_entity_gains_a_lei_and_keeps_its_key(
        self, session
    ) -> None:
        cfg = _cfg()
        entity = await _entity(
            session, cfg, key="listing:XCSE:PNDORA", name="Pandora A/S"
        )
        await _listed(session, cfg, entity=entity, ticker="PNDORA", exchange="CO")
        await session.commit()

        gleif = GleifIdentifierSource(
            provider=FakeGleifProvider(
                by_name={
                    "pandora a/s": [
                        FakeGleifRecord(lei=LEI_A, legal_name="Pandora A/S")
                    ]
                }
            )
        )
        report = await promote_entity_identity(
            session, legal_entity_id=entity.id, sources=[gleif], cfg=cfg
        )
        await session.commit()

        assert report.gained_identity is True
        assert len(report.accepted) == 1
        assert report.rejected == [] and report.withheld == []
        assert report.sources_consulted == [GLEIF_SOURCE_ID]

        await session.refresh(entity)
        assert entity.entity_key == "listing:XCSE:PNDORA", (
            "promotion adds an identifier; it never renumbers the identity"
        )
        row = (await session.execute(select(EntityIdentifier))).scalar_one()
        assert row.scheme == SCHEME_LEI and row.value_normalized == LEI_A
        assert row.checksum_verified is True

    async def test_a_withheld_finding_reaches_the_report(self, session) -> None:
        cfg = _cfg()
        entity = await _entity(session, cfg, key="listing:XCSE:PND", name="Pandora")
        await session.commit()

        gleif = GleifIdentifierSource(
            provider=FakeGleifProvider(
                by_name={
                    "pandora": [
                        FakeGleifRecord(lei=LEI_A, legal_name="Pandora A/S"),
                        FakeGleifRecord(lei=LEI_B, legal_name="Pandora Media, Inc."),
                    ]
                }
            )
        )
        report = await promote_entity_identity(
            session, legal_entity_id=entity.id, sources=[gleif], cfg=cfg
        )
        await session.commit()

        assert report.gained_identity is False
        assert report.withheld_reasons == [WITHHELD_AMBIGUOUS_NAME_MATCH]
        count = (
            await session.execute(select(func.count()).select_from(EntityIdentifier))
        ).scalar_one()
        assert count == 0

    async def test_a_rejected_claim_is_kept_with_its_reason(self, session) -> None:
        # The Boeing rule, through the whole chain: a source that does not know
        # better offers a CIK for an LSE listing, and the gate refuses it.
        cfg = _cfg()
        bae = await _entity(session, cfg, key="listing:XLON:BA", name="BAE Systems plc")
        await _listed(session, cfg, entity=bae, ticker="BA", exchange="LSE")
        await session.commit()

        naive = StaticIdentifierSource(
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
        report = await promote_entity_identity(
            session,
            legal_entity_id=bae.id,
            sources=[naive],
            cfg=cfg,
            query=IdentifierQuery(ticker="BA", exchange="LSE"),
        )
        await session.commit()

        assert report.accepted == []
        assert report.rejection_reasons == [REJECTED_VENUE_NOT_SEC_ELIGIBLE]
        count = (
            await session.execute(select(func.count()).select_from(EntityIdentifier))
        ).scalar_one()
        assert count == 0

    async def test_a_failing_source_does_not_stop_the_others(self, session) -> None:
        # A registry timing out is a normal Tuesday. Letting it abort the promotion
        # of every other identifier would make the mechanism as reliable as its
        # least reliable dependency.
        cfg = _cfg()
        entity = await _entity(
            session, cfg, key="listing:XNYS:BA", name="The Boeing Company"
        )
        await _listed(session, cfg, entity=entity, ticker="BA", exchange="NYSE")
        await session.commit()

        broken = StaticIdentifierSource(
            source_id="broken",
            schemes=frozenset({SCHEME_LEI}),
            raises=RuntimeError("connection reset"),
        )
        working = SecIdentifierSource(
            provider=FakeSecProvider(ciks={("BA", "NYSE"): "0000012927"})
        )
        report = await promote_entity_identity(
            session,
            legal_entity_id=entity.id,
            sources=[broken, working],
            cfg=cfg,
            query=IdentifierQuery(ticker="BA", exchange="NYSE"),
        )
        await session.commit()

        assert report.sources_consulted == ["broken", SEC_SOURCE_ID]
        assert report.withheld_reasons == [WITHHELD_SOURCE_ERROR]
        assert "connection reset" in report.withheld[0].detail
        assert len(report.accepted) == 1, "the working source still ran"

    async def test_the_default_query_comes_from_the_entity_itself(
        self, session
    ) -> None:
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="Pandora A/S")
        await session.commit()
        provider = FakeGleifProvider(
            by_name={"pandora a/s": [FakeGleifRecord(lei=LEI_A, legal_name="Pandora A/S")]}
        )
        report = await promote_entity_identity(
            session,
            legal_entity_id=entity.id,
            sources=[GleifIdentifierSource(provider=provider)],
            cfg=cfg,
        )
        await session.commit()
        assert provider.calls == ["search_by_name:Pandora A/S"]
        assert report.gained_identity is True

    async def test_a_second_pass_is_idempotent(self, session) -> None:
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="Pandora A/S")
        await session.commit()
        source = GleifIdentifierSource(
            provider=FakeGleifProvider(
                by_name={
                    "pandora a/s": [FakeGleifRecord(lei=LEI_A, legal_name="Pandora A/S")]
                }
            )
        )
        first = await promote_entity_identity(
            session, legal_entity_id=entity.id, sources=[source], cfg=cfg
        )
        await session.commit()
        second = await promote_entity_identity(
            session, legal_entity_id=entity.id, sources=[source], cfg=cfg
        )
        await session.commit()
        assert first.gained_identity and second.gained_identity
        assert (
            first.accepted[0].identifier_id == second.accepted[0].identifier_id
        )
        count = (
            await session.execute(select(func.count()).select_from(EntityIdentifier))
        ).scalar_one()
        assert count == 1

    async def test_a_nonexistent_entity_consults_nothing(self, session) -> None:
        cfg = _cfg()
        provider = FakeGleifProvider()
        report = await promote_entity_identity(
            session,
            legal_entity_id=uuid.uuid4(),
            sources=[GleifIdentifierSource(provider=provider)],
            cfg=cfg,
        )
        assert report.sources_consulted == []
        assert provider.calls == []
        assert "no such legal entity" in report.notes[0]

    async def test_with_the_flag_off_no_source_is_consulted(self, session) -> None:
        off = _cfg(v3_entity_master_enabled=False)
        provider = FakeGleifProvider()
        report = await promote_entity_identity(
            session,
            legal_entity_id=uuid.uuid4(),
            sources=[GleifIdentifierSource(provider=provider)],
            cfg=off,
        )
        assert report.sources_consulted == []
        assert provider.calls == []
        assert report.gained_identity is False
