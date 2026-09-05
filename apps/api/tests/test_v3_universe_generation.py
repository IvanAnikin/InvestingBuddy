"""Provider-neutral universe generation — V3.2 Slice 2.5.

WHAT THESE TESTS PIN
====================
  * de-duplication is by LISTING identity, not by ticker string — `BA`@LSE and
    `BA`@NYSE are two members, which is the whole reason this waits for 2.1;
  * two proposals merge only on EVIDENCE: a shared listing, or both resolving to
    one legal entity on actionable evidence. An `ambiguous` or `unresolved`
    resolution never merges;
  * an unresolved candidate still ENTERS the universe — a company nobody has seen
    is what a universe is for — with its state recorded so nothing downstream can
    mistake it for identified;
  * the hard cap of 50 is enforced BEFORE anything expensive sees a candidate, and
    the truncation is reported rather than silent;
  * a vague thesis still produces no universe;
  * a failing provider does not stop the others;
  * the curated registry's own filters are REUSED, not reimplemented;
  * with the flag off, nothing is consulted and the V2 path is untouched.

Real database for the identity paths. No network anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import company as _company  # noqa: F401
from app.models import legal_entity as _legal_entity  # noqa: F401
from app.models.legal_entity import LegalEntity, SecurityListing
from app.services.entities.master import (
    EntityInput,
    upsert_legal_entity,
    upsert_listing,
    upsert_security,
    venue_key_for,
)
from app.services.entities.resolution import resolve
from app.services.entities.universe import (
    HARD_MAX_UNIVERSE_SIZE,
    ProviderResult,
    UniverseCandidate,
    UniverseProvider,
    UniverseRequest,
    UniverseSet,
    build_universe_from_providers,
)
from app.services.entities.universe_providers import (
    CURATED_PROVIDER_ID,
    ENTITY_MASTER_PROVIDER_ID,
    CuratedRegistryUniverseProvider,
    EntityMasterUniverseProvider,
)
from app.services.entities.vocabulary import LISTING_ACTIVE, RESOLUTION_UNRESOLVED
from app.services.market_thesis_parser import parse_thesis
from tests.helpers.source_scan import identifiers_in


def _cfg(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "v3_entity_master_enabled": True,
        "v3_universe_providers_enabled": True,
    }
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


@dataclass
class FakeProvider:
    """A provider that returns exactly what it was given."""

    provider_id: str
    source_tier: str = "T9_fixture"
    result: ProviderResult = field(default_factory=ProviderResult)
    raises: Exception | None = None
    calls: int = 0

    async def candidates(self, request: UniverseRequest) -> ProviderResult:
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.result


def _candidate(ticker: str, exchange: str, name: str | None = None, **kw: object) -> UniverseCandidate:
    return UniverseCandidate(ticker=ticker, exchange=exchange, company_name=name, **kw)  # type: ignore[arg-type]


async def _listed_entity(
    session, cfg: Settings, *, key: str, name: str, ticker: str, exchange: str
) -> LegalEntity:
    entity = await upsert_legal_entity(
        session, EntityInput(entity_key=key, legal_name=name), cfg=cfg
    )
    assert entity is not None
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
    return entity


# --------------------------------------------------------------------------- #
# Identity, not a ticker string
# --------------------------------------------------------------------------- #


class TestListingIdentityDeduplication:
    async def test_one_ticker_on_two_venues_is_two_members(self, session) -> None:
        # The reason universe generation waits for slice 2.1. Before it, whether these
        # were one member or two depended on whoever wrote the de-duplication.
        cfg = _cfg()
        provider = FakeProvider(
            provider_id="fixture",
            result=ProviderResult(
                candidates=[
                    _candidate("BA", "LSE", "BAE Systems plc"),
                    _candidate("BA", "NYSE", "The Boeing Company"),
                ]
            ),
        )
        result = await build_universe_from_providers(
            [provider], UniverseRequest(), cfg=cfg
        )
        assert len(result.members) == 2
        assert {m.company_name for m in result.members} == {
            "BAE Systems plc",
            "The Boeing Company",
        }
        assert {m.listing_key for m in result.members} == {
            (venue_key_for("LSE"), "BA"),
            (venue_key_for("NYSE"), "BA"),
        }

    async def test_two_providers_naming_one_venue_differently_produce_one_member(
        self, session
    ) -> None:
        # The venue key comes from exchange_registry, so "NASDAQ" and "US" are the
        # same venue and the two proposals are one company.
        cfg = _cfg()
        first = FakeProvider(
            provider_id="alpha",
            result=ProviderResult(candidates=[_candidate("MRNA", "NASDAQ", "Moderna, Inc.")]),
        )
        second = FakeProvider(
            provider_id="beta",
            result=ProviderResult(candidates=[_candidate("MRNA", "US", None, sector="Health Care")]),
        )
        result = await build_universe_from_providers(
            [first, second], UniverseRequest(), cfg=cfg
        )
        assert len(result.members) == 1
        member = result.members[0]
        assert member.contributing_sources == ["alpha", "beta"]
        # And the second provider filled a gap rather than overwriting a value.
        assert member.company_name == "Moderna, Inc."
        assert member.sector == "Health Care"

    async def test_a_later_provider_never_overwrites_what_a_better_one_established(
        self, session
    ) -> None:
        cfg = _cfg()
        good = FakeProvider(
            provider_id="curated",
            result=ProviderResult(
                candidates=[
                    _candidate(
                        "PNDORA",
                        "CO",
                        "Pandora A/S",
                        sector="Consumer Discretionary",
                        relevance_reason="hand-verified luxury registry entry",
                    )
                ]
            ),
        )
        worse = FakeProvider(
            provider_id="generated",
            result=ProviderResult(
                candidates=[
                    _candidate(
                        "PNDORA",
                        "CO",
                        "PNDORA",
                        sector="Unknown",
                        relevance_reason="appeared in a list",
                    )
                ]
            ),
        )
        result = await build_universe_from_providers(
            [good, worse], UniverseRequest(), cfg=cfg
        )
        member = result.members[0]
        assert member.company_name == "Pandora A/S"
        assert member.sector == "Consumer Discretionary"
        assert member.relevance_reason == "hand-verified luxury registry entry"


class TestMergingOnlyOnEvidence:
    async def test_two_listings_of_one_resolved_entity_become_one_member(
        self, session
    ) -> None:
        cfg = _cfg()
        entity = await _listed_entity(
            session,
            cfg,
            key="lei:ASML",
            name="ASML Holding N.V.",
            ticker="ASML",
            exchange="NASDAQ",
        )
        # A second venue for the SAME security.
        security = (
            await session.execute(select(SecurityListing))
        ).scalar_one().security_id
        await upsert_listing(
            session,
            security_id=security,
            ticker="ASML",
            exchange="AS",
            cfg=cfg,
            listing_status=LISTING_ACTIVE,
        )
        await session.commit()

        provider = FakeProvider(
            provider_id="fixture",
            result=ProviderResult(
                candidates=[
                    _candidate("ASML", "NASDAQ", "ASML Holding N.V."),
                    _candidate("ASML", "AS", "ASML Holding N.V."),
                ]
            ),
        )
        result = await build_universe_from_providers(
            [provider],
            UniverseRequest(),
            cfg=cfg,
            resolver=resolve,
            session=session,
        )
        assert len(result.members) == 1, (
            "merging is safe here and only here: both sides resolved to one entity"
        )
        assert result.members[0].legal_entity_id == entity.id
        assert result.members[0].is_identified is True

    async def test_an_unresolved_candidate_enters_but_is_not_identified(
        self, session
    ) -> None:
        # A company the platform has never seen is exactly what a universe is for.
        cfg = _cfg()
        provider = FakeProvider(
            provider_id="fixture",
            result=ProviderResult(candidates=[_candidate("NEWCO", "CO", "New Co A/S")]),
        )
        result = await build_universe_from_providers(
            [provider],
            UniverseRequest(),
            cfg=cfg,
            resolver=resolve,
            session=session,
        )
        assert len(result.members) == 1
        member = result.members[0]
        assert member.resolution_state == RESOLUTION_UNRESOLVED
        assert member.legal_entity_id is None
        assert member.is_identified is False
        assert result.identified_members == []

    async def test_an_ambiguous_resolution_never_merges_two_candidates(
        self, session
    ) -> None:
        # Two entities share a name; a name-only match is `ambiguous`, so neither
        # candidate is identified and neither can absorb the other.
        cfg = _cfg()
        for key in ("lei:A", "lei:B"):
            await upsert_legal_entity(
                session,
                EntityInput(entity_key=key, legal_name="Acme Holdings"),
                cfg=cfg,
            )
        await session.commit()

        provider = FakeProvider(
            provider_id="fixture",
            result=ProviderResult(
                candidates=[
                    _candidate("ACM", "CO", "Acme Holdings"),
                    _candidate("ACME", "LSE", "Acme Holdings"),
                ]
            ),
        )
        result = await build_universe_from_providers(
            [provider],
            UniverseRequest(),
            cfg=cfg,
            resolver=resolve,
            session=session,
        )
        assert len(result.members) == 2, (
            "an unresolvable pair stays two members; a wrong merge researches one "
            "company under another's name"
        )
        assert all(m.is_identified is False for m in result.members)
        assert all(m.resolution_state == "ambiguous" for m in result.members)

    async def test_resolution_is_optional_and_its_absence_is_not_an_error(
        self, session
    ) -> None:
        # A caller with no database still gets a universe; it simply carries no
        # identity, and says so.
        cfg = _cfg()
        provider = FakeProvider(
            provider_id="fixture",
            result=ProviderResult(candidates=[_candidate("PNDORA", "CO", "Pandora A/S")]),
        )
        result = await build_universe_from_providers(
            [provider], UniverseRequest(), cfg=cfg
        )
        assert len(result.members) == 1
        assert result.members[0].resolution_state == RESOLUTION_UNRESOLVED

    async def test_a_resolver_that_raises_does_not_lose_the_candidate(
        self, session
    ) -> None:
        cfg = _cfg()

        async def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("database gone")

        provider = FakeProvider(
            provider_id="fixture",
            result=ProviderResult(candidates=[_candidate("PNDORA", "CO", "Pandora A/S")]),
        )
        result = await build_universe_from_providers(
            [provider], UniverseRequest(), cfg=cfg, resolver=boom, session=session
        )
        assert len(result.members) == 1
        assert "identity resolution failed" in result.members[0].warnings[0]


# --------------------------------------------------------------------------- #
# The cap
# --------------------------------------------------------------------------- #


class TestTheCapIsThePoint:
    async def test_the_cap_stops_the_universe_and_says_so(self, session) -> None:
        # A research run measures 261-451s. A universe of a thousand companies is not
        # a slow feature, it is an outage.
        cfg = _cfg()
        provider = FakeProvider(
            provider_id="fixture",
            result=ProviderResult(
                candidates=[_candidate(f"T{i}", "CO", f"Company {i}") for i in range(30)]
            ),
        )
        result = await build_universe_from_providers(
            [provider], UniverseRequest(max_size=5), cfg=cfg
        )
        assert len(result.members) == 5
        assert result.truncated_by_cap is True
        assert len(result.excluded) == 25
        assert all("cap of 5" in e["reason"] for e in result.excluded)

    async def test_a_merge_is_not_refused_for_a_full_universe(self, session) -> None:
        # A candidate that merges into an accepted member adds NO member, so refusing
        # it for "cap reached" throws away information the universe could absorb for
        # free — and reports an exclusion for a company that is in fact present.
        cfg = _cfg()
        first = FakeProvider(
            provider_id="alpha",
            result=ProviderResult(
                candidates=[_candidate(f"T{i}", "CO", f"Company {i}") for i in range(3)]
            ),
        )
        second = FakeProvider(
            provider_id="beta",
            result=ProviderResult(
                candidates=[_candidate("T0", "CO", None, sector="Industrials")]
            ),
        )
        result = await build_universe_from_providers(
            [first, second], UniverseRequest(max_size=3), cfg=cfg
        )
        assert len(result.members) == 3
        merged = next(m for m in result.members if m.ticker == "T0")
        assert merged.sector == "Industrials", "the merge happened despite a full cap"
        assert merged.contributing_sources == ["alpha", "beta"]
        assert result.excluded == [], "and no spurious exclusion was reported"

    async def test_metadata_not_sourced_is_only_ever_cleared(self, session) -> None:
        cfg = _cfg()
        thin = FakeProvider(
            provider_id="thin",
            result=ProviderResult(
                candidates=[_candidate("PNDORA", "CO", None, metadata_not_sourced=True)]
            ),
        )
        rich = FakeProvider(
            provider_id="rich",
            result=ProviderResult(
                candidates=[
                    _candidate(
                        "PNDORA", "CO", "Pandora A/S", sector="Consumer Discretionary"
                    )
                ]
            ),
        )
        result = await build_universe_from_providers(
            [thin, rich], UniverseRequest(), cfg=cfg
        )
        assert result.members[0].metadata_not_sourced is False

        # And the other way round it is NOT set: a provider knowing less is not
        # evidence that the metadata is unavailable.
        back = await build_universe_from_providers(
            [rich, thin], UniverseRequest(), cfg=cfg
        )
        assert back.members[0].metadata_not_sourced is False

    async def test_the_hard_ceiling_cannot_be_raised_by_a_caller(self) -> None:
        assert UniverseRequest(max_size=10_000).cap == HARD_MAX_UNIVERSE_SIZE
        assert UniverseRequest(max_size=0).cap >= 1
        assert UniverseRequest(max_size=-5).cap >= 1

    async def test_the_cap_is_applied_across_providers_not_per_provider(
        self, session
    ) -> None:
        cfg = _cfg()
        providers = [
            FakeProvider(
                provider_id=f"p{n}",
                result=ProviderResult(
                    candidates=[
                        _candidate(f"P{n}T{i}", "CO", f"P{n} Co {i}") for i in range(4)
                    ]
                ),
            )
            for n in range(3)
        ]
        result = await build_universe_from_providers(
            providers, UniverseRequest(max_size=5), cfg=cfg
        )
        assert len(result.members) == 5
        assert result.truncated_by_cap is True

    async def test_nothing_in_the_module_triggers_analysis_or_a_fetch(self) -> None:
        # The composer produces a bounded search space. It must not be the thing that
        # decides to spend 451 seconds on any of it.
        #
        # Checked against identifiers in EXECUTABLE position, not against the text: a
        # substring scan fires on the docstring that explains the rule, and a test
        # that fails when somebody documents what it enforces gets deleted. Same
        # helper, same lesson, as the backfill and OpenFIGI guards.
        source = (
            Path(__file__).resolve().parents[1] / "app/services/entities/universe.py"
        ).read_text(encoding="utf-8")
        used = identifiers_in(source)
        for banned in (
            "httpx",
            "requests",
            "aiohttp",
            "urllib",
            "run_company_research",
            "analyze_company",
            "enrich_company_profile",
            "build_universe",
        ):
            assert banned not in used, f"{banned} must not be reachable from here"


# --------------------------------------------------------------------------- #
# Guardrails carried forward, and containment
# --------------------------------------------------------------------------- #


class TestGuardrails:
    async def test_a_vague_thesis_still_produces_no_universe(self, session) -> None:
        cfg = _cfg()
        provider = FakeProvider(
            provider_id="fixture",
            result=ProviderResult(candidates=[_candidate("X", "CO", "X A/S")]),
        )
        result = await build_universe_from_providers(
            [provider],
            UniverseRequest(parsed_thesis={"needs_narrowing": True}),
            cfg=cfg,
        )
        assert result.needs_narrowing is True
        assert result.members == []
        assert provider.calls == 0, "a vague thesis consults nothing"

    async def test_a_failing_provider_does_not_stop_the_others(self, session) -> None:
        cfg = _cfg()
        broken = FakeProvider(provider_id="broken", raises=RuntimeError("boom"))
        working = FakeProvider(
            provider_id="working",
            result=ProviderResult(candidates=[_candidate("PNDORA", "CO", "Pandora A/S")]),
        )
        result = await build_universe_from_providers(
            [broken, working], UniverseRequest(), cfg=cfg
        )
        assert len(result.members) == 1
        errors = [c.error for c in result.contributions if c.error]
        assert len(errors) == 1 and "boom" in errors[0]
        assert any("contributed nothing" in w for w in result.warnings)

    async def test_every_member_records_which_provider_supplied_it(
        self, session
    ) -> None:
        cfg = _cfg()
        provider = FakeProvider(
            provider_id="fixture",
            source_tier="T9_fixture",
            result=ProviderResult(candidates=[_candidate("PNDORA", "CO", "Pandora A/S")]),
        )
        result = await build_universe_from_providers(
            [provider], UniverseRequest(), cfg=cfg
        )
        member = result.members[0]
        assert member.universe_source == "fixture"
        assert member.source_tier == "T9_fixture"
        summary = result.to_dict()["source_summary"]
        assert summary["providers"][0]["provider_id"] == "fixture"
        assert summary["providers"][0]["accepted"] == 1

    async def test_the_dict_form_keeps_the_shape_the_existing_scan_consumes(
        self, session
    ) -> None:
        # V3 fields are ADDED, not substituted, so a downstream reader that knows
        # nothing about entities keeps working.
        cfg = _cfg()
        provider = FakeProvider(
            provider_id="fixture",
            result=ProviderResult(candidates=[_candidate("PNDORA", "CO", "Pandora A/S")]),
        )
        result = await build_universe_from_providers(
            [provider], UniverseRequest(), cfg=cfg
        )
        item = result.to_dict()["items"][0]
        for key in (
            "ticker",
            "company_name",
            "exchange",
            "country",
            "region",
            "sector",
            "industry",
            "theme",
            "matched_keywords",
            "relevance_reason",
            "universe_source",
            "source_tier",
            "relevance_score_pre_scan",
            "metadata_not_sourced",
            "warnings",
        ):
            assert key in item, key
        assert item["resolution_state"] == RESOLUTION_UNRESOLVED
        assert item["legal_entity_id"] is None


# --------------------------------------------------------------------------- #
# The two real providers
# --------------------------------------------------------------------------- #


class TestCuratedRegistryProvider:
    async def test_it_satisfies_the_protocol_and_reuses_the_existing_builder(
        self, session
    ) -> None:
        cfg = _cfg()
        provider = CuratedRegistryUniverseProvider()
        assert isinstance(provider, UniverseProvider)
        assert provider.provider_id == CURATED_PROVIDER_ID

        parsed = parse_thesis("defense companies").to_dict()
        result = await build_universe_from_providers(
            [provider], UniverseRequest(parsed_thesis=parsed, max_size=10), cfg=cfg
        )
        assert result.members, "a real theme must produce a real universe"
        assert all(m.universe_source == CURATED_PROVIDER_ID for m in result.members)
        # The builder's own reason survives, which is what "reused, not
        # reimplemented" means in practice.
        assert all(m.relevance_reason for m in result.members)

    async def test_the_builders_region_filter_is_the_one_that_runs(
        self, session
    ) -> None:
        # Not a second implementation of the filter: the exclusions come back with
        # the builder's own reasons.
        cfg = _cfg()
        parsed = parse_thesis("European defense companies").to_dict()
        result = await build_universe_from_providers(
            [CuratedRegistryUniverseProvider()],
            UniverseRequest(parsed_thesis=parsed, max_size=25),
            cfg=cfg,
        )
        assert result.excluded, "US defence names must be excluded from a European scan"
        assert any("region mismatch" in e.get("reason", "") for e in result.excluded)


class TestEntityMasterProvider:
    async def test_it_offers_every_currently_listed_issuer(self, session) -> None:
        cfg = _cfg()
        await _listed_entity(
            session, cfg, key="lei:P", name="Pandora A/S", ticker="PNDORA", exchange="CO"
        )
        await _listed_entity(
            session, cfg, key="lei:A", name="ASML Holding N.V.", ticker="ASML",
            exchange="AS",
        )
        await session.commit()

        provider = EntityMasterUniverseProvider(session=session, cfg=cfg)
        assert isinstance(provider, UniverseProvider)
        result = await build_universe_from_providers(
            [provider], UniverseRequest(), cfg=cfg, resolver=resolve, session=session
        )
        assert {m.ticker for m in result.members} == {"PNDORA", "ASML"}
        assert all(m.universe_source == ENTITY_MASTER_PROVIDER_ID for m in result.members)
        assert all(m.is_identified for m in result.members), (
            "its own members resolve, because it produced them from their listings"
        )

    async def test_it_claims_no_thesis_relevance_it_did_not_compute(
        self, session
    ) -> None:
        # Otherwise a downstream ranking reads "we have seen this company" as
        # "this company fits the thesis".
        cfg = _cfg()
        await _listed_entity(
            session, cfg, key="lei:P", name="Pandora A/S", ticker="PNDORA", exchange="CO"
        )
        await session.commit()
        result = await (
            EntityMasterUniverseProvider(session=session, cfg=cfg)
        ).candidates(UniverseRequest())
        member = result.candidates[0]
        assert member.theme is None
        assert member.relevance_score_pre_scan == 0.0
        assert "no thesis relevance computed" in member.relevance_reason
        assert member.metadata_not_sourced is True, "it does not invent a sector"

    async def test_a_closed_listing_is_not_in_the_universe(self, session) -> None:
        cfg = _cfg()
        await _listed_entity(
            session, cfg, key="lei:G", name="Gone A/S", ticker="GONE", exchange="CO"
        )
        listing = (await session.execute(select(SecurityListing))).scalar_one()
        from datetime import date

        listing.effective_to = date(2026, 6, 30)
        await session.commit()

        result = await (
            EntityMasterUniverseProvider(session=session, cfg=cfg)
        ).candidates(UniverseRequest())
        assert result.candidates == []

    async def test_the_query_itself_is_bounded_by_the_cap(self, session) -> None:
        # Loading the table and slicing afterwards has already paid for the scan the
        # cap exists to prevent.
        cfg = _cfg()
        for n in range(8):
            await _listed_entity(
                session,
                cfg,
                key=f"lei:{n}",
                name=f"Company {n}",
                ticker=f"C{n}",
                exchange="CO",
            )
        await session.commit()
        result = await (
            EntityMasterUniverseProvider(session=session, cfg=cfg)
        ).candidates(UniverseRequest(max_size=3))
        assert len(result.candidates) == 3

    async def test_with_the_entity_master_off_it_holds_no_universe(
        self, session
    ) -> None:
        off = _cfg(v3_entity_master_enabled=False)
        result = await (
            EntityMasterUniverseProvider(session=session, cfg=off)
        ).candidates(UniverseRequest())
        assert result.candidates == []
        assert "entity master is disabled" in result.warnings[0]

    async def test_it_can_be_restricted_to_named_venues(self, session) -> None:
        cfg = _cfg()
        await _listed_entity(
            session, cfg, key="lei:P", name="Pandora A/S", ticker="PNDORA", exchange="CO"
        )
        await _listed_entity(
            session, cfg, key="lei:B", name="BAE Systems plc", ticker="BA",
            exchange="LSE",
        )
        await session.commit()
        result = await (
            EntityMasterUniverseProvider(
                session=session, cfg=cfg, exchange_codes=("CO",)
            )
        ).candidates(UniverseRequest())
        assert [c.ticker for c in result.candidates] == ["PNDORA"]


class TestBothProvidersTogether:
    async def test_the_curated_source_leads_and_the_platform_record_fills_in(
        self, session
    ) -> None:
        cfg = _cfg()
        parsed = parse_thesis("defense companies").to_dict()
        # One curated name is already in the entity master.
        await _listed_entity(
            session,
            cfg,
            key="lei:LMT",
            name="Lockheed Martin Corp.",
            ticker="LMT",
            exchange="US",
        )
        await session.commit()

        result = await build_universe_from_providers(
            [
                CuratedRegistryUniverseProvider(),
                EntityMasterUniverseProvider(session=session, cfg=cfg),
            ],
            UniverseRequest(parsed_thesis=parsed, max_size=25),
            cfg=cfg,
            resolver=resolve,
            session=session,
        )
        lmt = [m for m in result.members if m.ticker == "LMT"]
        assert len(lmt) == 1, "one company, two sources, one member"
        assert lmt[0].contributing_sources == [
            CURATED_PROVIDER_ID,
            ENTITY_MASTER_PROVIDER_ID,
        ]
        # The curated relevance reason wins; identity comes from the entity master.
        assert "no thesis relevance computed" not in lmt[0].relevance_reason
        assert lmt[0].is_identified is True


class TestDisabledByDefault:
    def test_the_flag_defaults_to_off(self) -> None:
        assert Settings().v3_universe_providers_enabled is False

    async def test_with_the_flag_off_no_provider_is_consulted(self, session) -> None:
        off = _cfg(v3_universe_providers_enabled=False)
        provider = FakeProvider(
            provider_id="fixture",
            result=ProviderResult(candidates=[_candidate("PNDORA", "CO", "Pandora A/S")]),
        )
        result = await build_universe_from_providers(
            [provider], UniverseRequest(), cfg=off
        )
        assert isinstance(result, UniverseSet)
        assert result.members == []
        assert provider.calls == 0
        assert "disabled" in result.warnings[0]

    async def test_the_v2_builder_is_untouched(self) -> None:
        # Demoted, not deleted, and not modified: build_universe still answers on its
        # own exactly as it does on main.
        from app.services.market_universe_builder import build_universe

        parsed = parse_thesis("defense companies").to_dict()
        built = build_universe(parsed, max_universe_size=10)
        assert built.items
        assert built.requested_max == 10
        assert all(
            item["universe_source"] == "curated_theme_registry" for item in built.items
        )
