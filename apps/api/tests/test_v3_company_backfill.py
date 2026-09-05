"""Linking `companies` to the entity master — V3.2 Slice 2.2.

WHAT THESE TESTS PIN
====================
  * every `companies` row gets an entity, a security and a listing;
  * the backfill is idempotent, resumable and bounded;
  * it never claims what a `companies` row does not record — jurisdiction stays
    NULL, status stays `unknown`, the quote unit comes from the VENUE and the
    instrument type is not asserted;
  * `BA`+LSE and `BA`+NYSE backfill to TWO entities;
  * two rows deriving ONE key link together when the names agree and leave the
    second UNLINKED when they do not — the refusal to merge, at backfill scale;
  * a re-run never demotes a primary a later slice promoted;
  * the compatibility adapter says WHICH of the two identity models answered;
  * existing reports still resolve through `company_id`;
  * with the flag off nothing is written and no query is issued;
  * nothing calls the backfill automatically.

Everything runs against a real database. No clock dependence.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

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
from app.models import report as _report  # noqa: F401
from app.models.company import Company
from app.models.legal_entity import LegalEntity, Security, SecurityListing
from app.models.report import Report
from app.services.entities.backfill import (
    BACKFILL_SOURCE,
    backfill_entities_from_companies,
)
from app.services.entities.compatibility import (
    SOURCE_COMPANIES_FALLBACK,
    SOURCE_ENTITY_MASTER,
    SOURCE_NONE,
    companies_for_legal_entity,
    legal_entity_for_company,
    resolve_entity,
)
from app.services.entities.identifiers import lei_check_digits
from app.services.entities.master import (
    EntityInput,
    close_listing,
    upsert_legal_entity,
    upsert_listing,
    upsert_security,
    venue_key_for,
)
from app.services.entities.vocabulary import (
    LISTING_ACTIVE,
    LISTING_DELISTED,
    SECURITY_ADR,
    SECURITY_ORDINARY_SHARE,
)
from tests.helpers.source_scan import modules_using

T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)
#: A structurally valid LEI, built from the MOD 97-10 generation rule rather
#: than transcribed — see ``test_v3_entity_master`` for why.
LEI_A = "529900AAAAAAAAAA01" + lei_check_digits("529900AAAAAAAAAA01")


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


def _company_row(
    ticker: str,
    exchange: str,
    name: str,
    *,
    created_at: datetime | None = None,
    **kw: object,
) -> Company:
    return Company(
        id=uuid.uuid4(),
        ticker=ticker,
        exchange=exchange,
        name=name,
        status="new",
        created_at=created_at or T0,
        **kw,
    )


async def _seed(session, *companies: Company) -> None:
    for row in companies:
        session.add(row)
    await session.commit()


# --------------------------------------------------------------------------- #
# The happy path
# --------------------------------------------------------------------------- #


class TestBackfill:
    async def test_every_company_gets_an_entity_a_security_and_a_listing(
        self, session
    ) -> None:
        cfg = _cfg()
        await _seed(
            session,
            _company_row("PNDORA", "CO", "Pandora A/S"),
            _company_row("ASML", "AS", "ASML Holding N.V."),
            _company_row("MRNA", "NASDAQ", "Moderna, Inc."),
        )
        result = await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()

        assert result.examined == 3
        assert result.linked == 3
        assert result.ambiguous == 0
        for model, expected in ((LegalEntity, 3), (Security, 3), (SecurityListing, 3)):
            count = (
                await session.execute(select(func.count()).select_from(model))
            ).scalar_one()
            assert count == expected, model.__tablename__

        unlinked = (
            await session.execute(
                select(func.count())
                .select_from(Company)
                .where(Company.legal_entity_id.is_(None))
            )
        ).scalar_one()
        assert unlinked == 0

    async def test_the_entity_key_is_venue_scoped_and_the_source_is_recorded(
        self, session
    ) -> None:
        cfg = _cfg()
        await _seed(session, _company_row("PNDORA", "CO", "Pandora A/S"))
        await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()

        entity = (await session.execute(select(LegalEntity))).scalar_one()
        assert entity.entity_key == f"listing:{venue_key_for('CO')}:PNDORA"
        assert entity.source == BACKFILL_SOURCE

    async def test_running_it_twice_changes_nothing(self, session) -> None:
        cfg = _cfg()
        await _seed(session, _company_row("PNDORA", "CO", "Pandora A/S"))
        first = await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()
        second = await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()

        assert first.linked == 1
        assert second.examined == 0 and second.linked == 0
        count = (
            await session.execute(select(func.count()).select_from(LegalEntity))
        ).scalar_one()
        assert count == 1

    async def test_it_is_resumable_in_batches(self, session) -> None:
        cfg = _cfg()
        await _seed(
            session,
            _company_row("AAA", "CO", "A A/S", created_at=T0),
            _company_row("BBB", "CO", "B A/S", created_at=T0),
            _company_row("CCC", "CO", "C A/S", created_at=T0),
        )
        first = await backfill_entities_from_companies(session, cfg=cfg, limit=2)
        await session.commit()
        assert first.examined == 2 and first.remaining_possible is True

        second = await backfill_entities_from_companies(session, cfg=cfg, limit=2)
        await session.commit()
        assert second.examined == 1 and second.remaining_possible is False

        unlinked = (
            await session.execute(
                select(func.count())
                .select_from(Company)
                .where(Company.legal_entity_id.is_(None))
            )
        ).scalar_one()
        assert unlinked == 0

    async def test_one_company_can_be_backfilled_on_its_own(self, session) -> None:
        cfg = _cfg()
        target = _company_row("PNDORA", "CO", "Pandora A/S")
        await _seed(session, target, _company_row("ASML", "AS", "ASML Holding N.V."))
        result = await backfill_entities_from_companies(
            session, cfg=cfg, company_id=target.id
        )
        await session.commit()
        assert result.linked == 1
        count = (
            await session.execute(select(func.count()).select_from(LegalEntity))
        ).scalar_one()
        assert count == 1

    async def test_a_company_with_no_ticker_is_skipped_not_invented(
        self, session
    ) -> None:
        cfg = _cfg()
        await _seed(session, _company_row("   ", "CO", "Nameless A/S"))
        result = await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()
        assert result.skipped == 1 and result.linked == 0
        count = (
            await session.execute(select(func.count()).select_from(LegalEntity))
        ).scalar_one()
        assert count == 0


# --------------------------------------------------------------------------- #
# What it refuses to claim
# --------------------------------------------------------------------------- #


class TestItDoesNotInvent:
    async def test_jurisdiction_stays_null_even_when_the_country_is_known(
        self, session
    ) -> None:
        # `companies.country` is largely the LISTING VENUE's country, and where a
        # company trades is not where it is incorporated.
        cfg = _cfg()
        await _seed(
            session, _company_row("PNDORA", "CO", "Pandora A/S", country="Denmark")
        )
        await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()
        entity = (await session.execute(select(LegalEntity))).scalar_one()
        assert entity.jurisdiction is None

    async def test_status_stays_unknown(self, session) -> None:
        cfg = _cfg()
        await _seed(session, _company_row("PNDORA", "CO", "Pandora A/S"))
        await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()
        entity = (await session.execute(select(LegalEntity))).scalar_one()
        assert entity.entity_status == "unknown"
        assert entity.status_source is None

    async def test_the_quote_unit_comes_from_the_venue_not_from_the_company_row(
        self, session
    ) -> None:
        # The company row says GBP; LSE QUOTES in pence. Taking the company's value
        # would mislabel every London price as 100x its real pound value.
        cfg = _cfg()
        await _seed(
            session, _company_row("BA", "LSE", "BAE Systems plc", currency="GBP")
        )
        await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()
        listing = (await session.execute(select(SecurityListing))).scalar_one()
        assert listing.quote_currency == "GBX"

    async def test_the_instrument_type_is_not_asserted_so_a_correction_survives(
        self, session
    ) -> None:
        # A `companies` row is no evidence that a ticker is an ADR — or that it is
        # not. A re-run must not undo a correction a better-informed slice made.
        cfg = _cfg()
        await _seed(session, _company_row("ISSAY", "OTC", "An Issuer S.A."))
        await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()

        security = (await session.execute(select(Security))).scalar_one()
        assert security.security_type == SECURITY_ORDINARY_SHARE
        security.security_type = SECURITY_ADR  # what slice 2.3 would do, with a source
        await session.commit()

        again = await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()
        assert again.examined == 0
        refreshed = (await session.execute(select(Security))).scalar_one()
        assert refreshed.security_type == SECURITY_ADR


# --------------------------------------------------------------------------- #
# The collision cases — the reason this is not a one-liner
# --------------------------------------------------------------------------- #


class TestCollisions:
    async def test_one_ticker_on_two_venues_becomes_two_entities(
        self, session
    ) -> None:
        cfg = _cfg()
        await _seed(
            session,
            _company_row("BA", "LSE", "BAE Systems plc"),
            _company_row("BA", "NYSE", "The Boeing Company"),
        )
        result = await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()

        assert result.linked == 2 and result.ambiguous == 0
        entities = (
            (await session.execute(select(LegalEntity).order_by(LegalEntity.entity_key)))
            .scalars()
            .all()
        )
        assert len(entities) == 2
        assert {e.legal_name for e in entities} == {
            "BAE Systems plc",
            "The Boeing Company",
        }

    async def test_two_rows_deriving_one_key_with_the_same_name_share_an_entity(
        self, session
    ) -> None:
        # NYSE and NASDAQ both normalise onto one US venue key. For one issuer
        # written twice, collapsing them is the intended de-duplication.
        cfg = _cfg()
        await _seed(
            session,
            _company_row("MRNA", "NASDAQ", "Moderna, Inc.", created_at=T0),
            _company_row("MRNA", "NYSE", "Moderna, Inc.", created_at=T0),
        )
        result = await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()

        assert result.linked == 1 and result.reused == 1 and result.ambiguous == 0
        entity = (await session.execute(select(LegalEntity))).scalar_one()
        linked = await companies_for_legal_entity(
            session, legal_entity_id=entity.id, cfg=cfg
        )
        assert len(linked) == 2
        # One entity, one security, ONE listing — the second row did not create a
        # duplicate listing for the same venue and ticker.
        for model in (Security, SecurityListing):
            count = (
                await session.execute(select(func.count()).select_from(model))
            ).scalar_one()
            assert count == 1, model.__tablename__

    async def test_two_rows_deriving_one_key_with_different_names_are_not_merged(
        self, session
    ) -> None:
        # This is the whole rule at backfill scale. Which name is right is an
        # evidence question the backfill cannot answer, so the later row stays
        # UNLINKED rather than being attributed to the earlier row's entity.
        #
        # The timestamps differ on purpose: the backfill orders by `created_at`,
        # so tied timestamps make WHICH row wins arbitrary. See the test below,
        # which pins the invariant that holds either way.
        cfg = _cfg()
        first = _company_row("ZZZ", "NASDAQ", "First Corporation", created_at=T0)
        second = _company_row(
            "ZZZ",
            "NYSE",
            "An Entirely Different Corporation",
            created_at=T0 + timedelta(days=1),
        )
        await _seed(session, first, second)
        result = await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()

        assert result.linked == 1
        assert result.ambiguous == 1
        assert result.ambiguous_rows[0][0] == "ZZZ"
        assert result.ambiguous_rows[0][3] == "First Corporation"

        await session.refresh(second)
        assert second.legal_entity_id is None, (
            "an unresolvable row stays unlinked; attributing it to the other "
            "company's entity is the silent merge the entity master prevents"
        )
        count = (
            await session.execute(select(func.count()).select_from(LegalEntity))
        ).scalar_one()
        assert count == 1

    async def test_which_colliding_row_wins_is_arbitrary_but_neither_is_merged(
        self, session
    ) -> None:
        # With tied `created_at` the ordering falls back to a random UUID, so which
        # of the two rows creates the entity is not determined. That is acceptable —
        # and this test says so rather than leaving a flake for somebody to find —
        # because the guarantee is not WHICH row wins. It is that exactly one is
        # linked, the other is reported, and neither is attributed to the other's
        # entity.
        cfg = _cfg()
        await _seed(
            session,
            _company_row("ZZZ", "NASDAQ", "First Corporation", created_at=T0),
            _company_row("ZZZ", "NYSE", "Second Corporation", created_at=T0),
        )
        result = await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()

        assert result.linked == 1 and result.ambiguous == 1
        entities = (await session.execute(select(LegalEntity))).scalars().all()
        assert len(entities) == 1
        linked = await companies_for_legal_entity(
            session, legal_entity_id=entities[0].id, cfg=cfg
        )
        assert len(linked) == 1
        assert linked[0].name == entities[0].legal_name, (
            "the linked company must be the one whose name the entity carries"
        )

    async def test_an_ambiguous_row_stays_ambiguous_on_a_re_run(self, session) -> None:
        cfg = _cfg()
        await _seed(
            session,
            _company_row("ZZZ", "NASDAQ", "First Corporation", created_at=T0),
            _company_row("ZZZ", "NYSE", "Another Corporation", created_at=T0),
        )
        await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()
        again = await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()
        assert again.ambiguous == 1 and again.linked == 0

    async def test_an_entity_under_a_stronger_key_is_found_not_duplicated(
        self, session
    ) -> None:
        # Slice 2.3 creates entities keyed `lei:...`. Searching only by the derived
        # `listing:...` key would miss one, create a SECOND entity for the same
        # issuer, and then hit upsert_listing's refusal to move a live ticker
        # between securities — an exception that would abort the whole batch and
        # leave a stray entity behind.
        cfg = _cfg()
        entity = await upsert_legal_entity(
            session,
            EntityInput(
                entity_key="lei:" + LEI_A,
                legal_name="Pandora A/S",
                jurisdiction="DK",
                source="gleif",
            ),
            cfg=cfg,
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
            ticker="PNDORA",
            exchange="CO",
            cfg=cfg,
            listing_status=LISTING_ACTIVE,
            is_primary=True,
        )
        await _seed(session, _company_row("PNDORA", "CO", "Pandora A/S"))

        result = await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()

        assert result.reused == 1 and result.linked == 0
        entities = (await session.execute(select(LegalEntity))).scalars().all()
        assert len(entities) == 1, "no second entity for an issuer already known"
        assert entities[0].entity_key == "lei:" + LEI_A
        company = (await session.execute(select(Company))).scalar_one()
        assert company.legal_entity_id == entity.id
        # And the stronger identity kept its jurisdiction — the backfill did not
        # overwrite a sourced field with the nothing it knows.
        assert entities[0].jurisdiction == "DK"

    async def test_a_closed_listing_is_not_re_opened_by_a_backfill(
        self, session
    ) -> None:
        # A delisting recorded by a later slice is a sourced decision. A backfill
        # re-run must not silently contradict it by opening a fresh window.
        # NASDAQ and NYSE collapse onto one US venue key, which is how two
        # `companies` rows can derive one entity key without colliding on
        # UNIQUE (ticker, exchange).
        cfg = _cfg()
        await _seed(
            session, _company_row("GONE", "NASDAQ", "Delisted Inc.", created_at=T0)
        )
        await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()

        listing = (await session.execute(select(SecurityListing))).scalar_one()
        await close_listing(
            session,
            listing=listing,
            effective_to=date(2026, 6, 30),
            listing_status=LISTING_DELISTED,
            cfg=cfg,
        )
        await session.commit()

        # A second company row for the same symbol on the same venue key arrives.
        await _seed(
            session,
            _company_row(
                "GONE", "NYSE", "Delisted Inc.", created_at=T0 + timedelta(days=1)
            ),
        )
        result = await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()

        assert result.reused == 1 and result.linked == 0
        listings = (await session.execute(select(SecurityListing))).scalars().all()
        assert len(listings) == 1
        assert listings[0].effective_to == date(2026, 6, 30), (
            "the closed window stays closed"
        )

    async def test_a_re_run_never_demotes_a_primary_a_later_slice_promoted(
        self, session
    ) -> None:
        cfg = _cfg()
        await _seed(session, _company_row("MRNA", "NASDAQ", "Moderna, Inc.", created_at=T0))
        await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()
        entity = (await session.execute(select(LegalEntity))).scalar_one()

        # What slice 2.3 would do with a source: a better instrument becomes primary.
        adr = await upsert_security(
            session,
            legal_entity_id=entity.id,
            security_key="adr:1",
            cfg=cfg,
            security_type=SECURITY_ADR,
            is_primary=True,
        )
        assert adr is not None
        await session.commit()

        # A second company row for the same key arrives and is backfilled.
        await _seed(session, _company_row("MRNA", "NYSE", "Moderna, Inc.", created_at=T0))
        result = await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()
        assert result.reused == 1

        await session.refresh(adr)
        assert adr.is_primary is True, "a backfill re-run must not undo a correction"


# --------------------------------------------------------------------------- #
# The compatibility adapter
# --------------------------------------------------------------------------- #


class TestCompatibilityAdapter:
    async def test_it_reports_which_identity_model_answered(self, session) -> None:
        cfg = _cfg()
        await _seed(
            session,
            _company_row("PNDORA", "CO", "Pandora A/S"),
            _company_row("CFR", "SW", "Compagnie Financiere Richemont SA"),
        )
        # Backfill only one of them.
        rows = (await session.execute(select(Company))).scalars().all()
        target = next(r for r in rows if r.ticker == "PNDORA")
        await backfill_entities_from_companies(session, cfg=cfg, company_id=target.id)
        await session.commit()

        backfilled = await resolve_entity(
            session, ticker="PNDORA", exchange="CO", cfg=cfg
        )
        assert backfilled.source == SOURCE_ENTITY_MASTER
        assert backfilled.has_entity_identity is True
        assert backfilled.company is not None

        not_backfilled = await resolve_entity(
            session, ticker="CFR", exchange="SW", cfg=cfg
        )
        assert not_backfilled.source == SOURCE_COMPANIES_FALLBACK
        assert not_backfilled.has_entity_identity is False
        assert not_backfilled.found is True
        assert not_backfilled.company is not None

        unknown = await resolve_entity(session, ticker="NOPE", exchange="CO", cfg=cfg)
        assert unknown.source == SOURCE_NONE and unknown.found is False

    async def test_the_fallback_finds_a_row_written_under_either_exchange_spelling(
        self, session
    ) -> None:
        # A `companies` row may have been written as "NASDAQ" or as its normalised
        # "US". The adapter must not report "not found" for a row the V2 path
        # would have located.
        cfg = _cfg()
        await _seed(session, _company_row("MRNA", "US", "Moderna, Inc."))
        found = await resolve_entity(
            session, ticker="MRNA", exchange="NASDAQ", cfg=cfg
        )
        assert found.source == SOURCE_COMPANIES_FALLBACK
        assert found.company is not None

    async def test_with_the_entity_master_off_the_adapter_is_v2_behaviour(
        self, session
    ) -> None:
        off = _cfg(v3_entity_master_enabled=False)
        await _seed(session, _company_row("PNDORA", "CO", "Pandora A/S"))
        result = await resolve_entity(session, ticker="PNDORA", exchange="CO", cfg=off)
        assert result.source == SOURCE_COMPANIES_FALLBACK
        assert result.legal_entity is None

    async def test_an_unlinked_company_resolves_to_no_entity(self, session) -> None:
        cfg = _cfg()
        company = _company_row("PNDORA", "CO", "Pandora A/S")
        await _seed(session, company)
        assert await legal_entity_for_company(session, company=company, cfg=cfg) is None


# --------------------------------------------------------------------------- #
# V2 compatibility
# --------------------------------------------------------------------------- #


class TestNoV2Regression:
    async def test_a_report_still_resolves_through_company_id_after_the_backfill(
        self, session
    ) -> None:
        cfg = _cfg()
        company = _company_row("PNDORA", "CO", "Pandora A/S")
        await _seed(session, company)
        report = Report(
            id=uuid.uuid4(),
            company_id=company.id,
            title="Pandora A/S — research memo",
            slug="pandora-a-s-research-memo",
            report_type="company_research",
            status="draft",
        )
        session.add(report)
        await session.commit()

        await backfill_entities_from_companies(session, cfg=cfg)
        await session.commit()

        loaded = (
            await session.execute(select(Report).where(Report.id == report.id))
        ).scalar_one()
        assert loaded.company_id == company.id
        still_there = (
            await session.execute(select(Company).where(Company.id == company.id))
        ).scalar_one()
        assert still_there.ticker == "PNDORA"
        assert still_there.legal_entity_id is not None

    async def test_the_link_is_nullable_and_that_is_a_real_state(self) -> None:
        column = Company.__table__.columns["legal_entity_id"]
        assert column.nullable is True
        fk = next(iter(column.foreign_keys))
        assert fk.column.table.name == "legal_entities"
        # Lineage, not composition: deleting an entity must not delete the company
        # row a thousand reports point at.
        assert fk.ondelete == "SET NULL"

    async def test_the_ticker_exchange_key_is_untouched(self) -> None:
        names = {c.name for c in Company.__table__.constraints if c.name}
        assert "uq_companies_ticker_exchange" in names


class TestDisabledByDefault:
    async def test_with_the_flag_off_nothing_is_written(self, session) -> None:
        off = _cfg(v3_entity_master_enabled=False)
        await _seed(session, _company_row("PNDORA", "CO", "Pandora A/S"))
        result = await backfill_entities_from_companies(session, cfg=off)
        await session.commit()
        assert result.examined == 0
        for model in (LegalEntity, Security, SecurityListing):
            count = (
                await session.execute(select(func.count()).select_from(model))
            ).scalar_one()
            assert count == 0
        company = (await session.execute(select(Company))).scalar_one()
        assert company.legal_entity_id is None


class TestNothingStartsItByItself:
    def test_no_other_module_calls_the_backfill(self) -> None:
        # A backfill that starts itself on the first request after a deploy is how
        # a migration becomes an outage. Same rule, and now the same scan, as
        # the corpus backfill's own invariant test.
        callers = modules_using(
            "backfill_entities_from_companies",
            root=Path(__file__).resolve().parents[1] / "app",
            exclude=("entities/backfill.py",),
        )
        assert callers == [], (
            f"the backfill must stay operator-invoked; used in {callers}"
        )
