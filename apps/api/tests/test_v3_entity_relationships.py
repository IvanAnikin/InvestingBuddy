"""Relationships, reporting scopes and segments — V3.2 Slice 2.4.

WHAT THESE TESTS PIN
====================
  * a relationship has exactly ONE canonical row whichever direction is asserted,
    and the inverse is a query with a correct label;
  * a self-relationship and a duplicate live pair are refused — by the schema, not
    only by the writer;
  * a closed window frees the pair and history still queries;
  * `reporting_scopes.scope_key` equals `FactScope.scope_key` — compared against the
    other implementation directly, not against a copied literal;
  * the CFR shape: a Specialist Watchmakers scope and a Group scope on one entity are
    distinct rows and neither can be read as the other;
  * a rename creates a SECOND scope, and linking the two is impossible without a
    source — the database refuses it even by direct INSERT;
  * two periods of one scope are two disclosures and one series;
  * a depositary receipt with no ratio makes per-share arithmetic REFUSE.

Real database. No clock dependence.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import company as _company  # noqa: F401
from app.models import legal_entity as _legal_entity  # noqa: F401
from app.models.legal_entity import (
    BusinessSegment,
    EntityRelationship,
    LegalEntity,
    ReportingScope,
    Security,
)
from app.services.entities.master import (
    EntityInput,
    upsert_legal_entity,
    upsert_security,
)
from app.services.entities.relationships import (
    close_relationship,
    parent_of,
    record_relationship,
    relationships_for,
    subsidiaries_of,
)
from app.services.entities.scopes import (
    PerShareNotPermittedError,
    ScopeInput,
    link_scope_rename,
    record_segment_disclosure,
    scope_key_for,
    scope_lineage,
    segment_series,
    underlying_shares_per_unit,
    upsert_reporting_scope,
)
from app.services.entities.vocabulary import (
    REL_JOINT_VENTURE_WITH,
    REL_PARENT_OF,
    SECURITY_ADR,
    SECURITY_ORDINARY_SHARE,
)
from app.services.sources.fact_scope import (
    SCOPE_TYPE_GROUP,
    SCOPE_TYPE_SEGMENT,
    FactScope,
    parse_scope,
)


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
        # SQLite ignores FK and CHECK enforcement unless asked.
        await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _entity(session, cfg: Settings, *, key: str, name: str) -> LegalEntity:
    entity = await upsert_legal_entity(
        session, EntityInput(entity_key=key, legal_name=name), cfg=cfg
    )
    assert entity is not None
    return entity


# --------------------------------------------------------------------------- #
# Relationships
# --------------------------------------------------------------------------- #


class TestCanonicalDirection:
    async def test_either_direction_produces_one_canonical_row(self, session) -> None:
        cfg = _cfg()
        parent = await _entity(session, cfg, key="lei:P", name="Parent Holdings plc")
        child = await _entity(session, cfg, key="lei:C", name="Child Operations Ltd")

        # Assert it as `subsidiary_of` — the writer normalises to `parent_of`.
        row = await record_relationship(
            session,
            subject_entity_id=child.id,
            object_entity_id=parent.id,
            relationship_type="subsidiary_of",
            source="annual_report_2025",
            cfg=cfg,
        )
        await session.commit()
        assert row is not None
        assert row.relationship_type == REL_PARENT_OF
        assert row.subject_entity_id == parent.id
        assert row.object_entity_id == child.id

        # Assert the same fact the other way round — still one row.
        again = await record_relationship(
            session,
            subject_entity_id=parent.id,
            object_entity_id=child.id,
            relationship_type="parent_of",
            source="annual_report_2025",
            cfg=cfg,
        )
        await session.commit()
        assert again is not None and again.id == row.id
        count = (
            await session.execute(
                select(func.count()).select_from(EntityRelationship)
            )
        ).scalar_one()
        assert count == 1, "storing both directions is how two rows come to disagree"

    async def test_each_side_reads_the_relationship_its_own_way(self, session) -> None:
        cfg = _cfg()
        parent = await _entity(session, cfg, key="lei:P", name="Parent Holdings plc")
        child = await _entity(session, cfg, key="lei:C", name="Child Operations Ltd")
        await record_relationship(
            session,
            subject_entity_id=parent.id,
            object_entity_id=child.id,
            relationship_type=REL_PARENT_OF,
            source="annual_report_2025",
            cfg=cfg,
        )
        await session.commit()

        from_parent = await relationships_for(
            session, legal_entity_id=parent.id, cfg=cfg
        )
        from_child = await relationships_for(session, legal_entity_id=child.id, cfg=cfg)
        assert [(r.label, r.entity.legal_name) for r in from_parent] == [
            ("parent_of", "Child Operations Ltd")
        ]
        assert [(r.label, r.entity.legal_name) for r in from_child] == [
            ("subsidiary_of", "Parent Holdings plc")
        ]
        # And the helpers agree with the labels.
        assert (await parent_of(session, legal_entity_id=child.id, cfg=cfg)) is not None
        subs = await subsidiaries_of(session, legal_entity_id=parent.id, cfg=cfg)
        assert [s.legal_name for s in subs] == ["Child Operations Ltd"]

    async def test_a_symmetric_relationship_is_ordered_so_it_cannot_double(
        self, session
    ) -> None:
        # "A joint_venture_with B" and "B joint_venture_with A" are one fact. Without
        # a deterministic ordering they would be two live rows and the unique index
        # could not see it.
        cfg = _cfg()
        a = await _entity(session, cfg, key="lei:A", name="Alpha S.A.")
        b = await _entity(session, cfg, key="lei:B", name="Beta N.V.")
        await record_relationship(
            session,
            subject_entity_id=a.id,
            object_entity_id=b.id,
            relationship_type=REL_JOINT_VENTURE_WITH,
            source="press_release",
            cfg=cfg,
        )
        await record_relationship(
            session,
            subject_entity_id=b.id,
            object_entity_id=a.id,
            relationship_type=REL_JOINT_VENTURE_WITH,
            source="press_release",
            cfg=cfg,
        )
        await session.commit()
        count = (
            await session.execute(
                select(func.count()).select_from(EntityRelationship)
            )
        ).scalar_one()
        assert count == 1

    async def test_an_unrecognised_relationship_type_is_refused(self, session) -> None:
        cfg = _cfg()
        a = await _entity(session, cfg, key="lei:A", name="Alpha S.A.")
        b = await _entity(session, cfg, key="lei:B", name="Beta N.V.")
        with pytest.raises(ValueError, match="not a recognised relationship type"):
            await record_relationship(
                session,
                subject_entity_id=a.id,
                object_entity_id=b.id,
                relationship_type="sort_of_owns",
                source="a_guess",
                cfg=cfg,
            )


class TestRelationshipRefusals:
    async def test_a_self_relationship_is_refused_by_the_writer(self, session) -> None:
        cfg = _cfg()
        a = await _entity(session, cfg, key="lei:A", name="Alpha S.A.")
        with pytest.raises(ValueError, match="cannot be related to itself"):
            await record_relationship(
                session,
                subject_entity_id=a.id,
                object_entity_id=a.id,
                relationship_type=REL_PARENT_OF,
                source="x",
                cfg=cfg,
            )

    async def test_and_by_the_schema(self, session) -> None:
        cfg = _cfg()
        a = await _entity(session, cfg, key="lei:A", name="Alpha S.A.")
        await session.commit()
        session.add(
            EntityRelationship(
                id=uuid.uuid4(),
                subject_entity_id=a.id,
                object_entity_id=a.id,
                relationship_type=REL_PARENT_OF,
                source="direct_insert",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async def test_an_unsourced_relationship_is_refused(self, session) -> None:
        cfg = _cfg()
        a = await _entity(session, cfg, key="lei:A", name="Alpha S.A.")
        b = await _entity(session, cfg, key="lei:B", name="Beta N.V.")
        with pytest.raises(ValueError, match="needs a source"):
            await record_relationship(
                session,
                subject_entity_id=a.id,
                object_entity_id=b.id,
                relationship_type=REL_PARENT_OF,
                source="   ",
                cfg=cfg,
            )

    async def test_confidence_outside_the_range_is_refused(self, session) -> None:
        cfg = _cfg()
        a = await _entity(session, cfg, key="lei:A", name="Alpha S.A.")
        b = await _entity(session, cfg, key="lei:B", name="Beta N.V.")
        with pytest.raises(ValueError, match="confidence"):
            await record_relationship(
                session,
                subject_entity_id=a.id,
                object_entity_id=b.id,
                relationship_type=REL_PARENT_OF,
                source="x",
                cfg=cfg,
                confidence=2.0,
            )

    async def test_a_duplicate_live_triple_is_refused_by_the_index(
        self, session
    ) -> None:
        cfg = _cfg()
        a = await _entity(session, cfg, key="lei:A", name="Alpha S.A.")
        b = await _entity(session, cfg, key="lei:B", name="Beta N.V.")
        await record_relationship(
            session,
            subject_entity_id=a.id,
            object_entity_id=b.id,
            relationship_type=REL_PARENT_OF,
            source="filing",
            cfg=cfg,
        )
        await session.commit()
        session.add(
            EntityRelationship(
                id=uuid.uuid4(),
                subject_entity_id=a.id,
                object_entity_id=b.id,
                relationship_type=REL_PARENT_OF,
                source="direct_insert",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async def test_closing_a_window_frees_the_pair_and_keeps_history(
        self, session
    ) -> None:
        # A subsidiary sold in 2024 was still a subsidiary in 2023, and every figure
        # consolidated then depends on that having been true.
        cfg = _cfg()
        a = await _entity(session, cfg, key="lei:A", name="Alpha S.A.")
        b = await _entity(session, cfg, key="lei:B", name="Beta N.V.")
        first = await record_relationship(
            session,
            subject_entity_id=a.id,
            object_entity_id=b.id,
            relationship_type=REL_PARENT_OF,
            source="filing_2023",
            cfg=cfg,
            effective_from=date(2023, 1, 1),
        )
        assert first is not None
        await close_relationship(
            session, relationship=first, effective_to=date(2024, 6, 30), cfg=cfg
        )
        await session.commit()

        assert await relationships_for(session, legal_entity_id=a.id, cfg=cfg) == []
        history = await relationships_for(
            session, legal_entity_id=a.id, cfg=cfg, include_closed=True
        )
        assert len(history) == 1 and history[0].is_current is False

        # And the pair can be re-established — a re-acquisition is representable.
        again = await record_relationship(
            session,
            subject_entity_id=a.id,
            object_entity_id=b.id,
            relationship_type=REL_PARENT_OF,
            source="filing_2026",
            cfg=cfg,
            effective_from=date(2026, 1, 1),
        )
        await session.commit()
        assert again is not None and again.id != first.id

    async def test_two_live_parents_is_not_an_answer(self, session) -> None:
        # Two live parents is a contradiction. Returning either would be a coin flip
        # presented as a corporate structure.
        cfg = _cfg()
        child = await _entity(session, cfg, key="lei:C", name="Child Ltd")
        for key, name in (("lei:P1", "First Parent"), ("lei:P2", "Second Parent")):
            parent = await _entity(session, cfg, key=key, name=name)
            await record_relationship(
                session,
                subject_entity_id=parent.id,
                object_entity_id=child.id,
                relationship_type=REL_PARENT_OF,
                source="filing",
                cfg=cfg,
            )
        await session.commit()
        assert await parent_of(session, legal_entity_id=child.id, cfg=cfg) is None


# --------------------------------------------------------------------------- #
# Reporting scopes — the key must BE fact_scope's key
# --------------------------------------------------------------------------- #


class TestScopeKeyMatchesFactScope:
    def test_group_and_segment_keys_come_from_fact_scope_itself(self) -> None:
        # Compared against the OTHER implementation, not against a copied literal:
        # a literal would keep agreeing after fact_scope changed.
        assert scope_key_for(SCOPE_TYPE_GROUP, None) == FactScope(
            scope_type=SCOPE_TYPE_GROUP
        ).scope_key
        for name in ("Specialist Watchmakers", "Jewellery Maisons", "Fashion & Others"):
            assert scope_key_for(SCOPE_TYPE_SEGMENT, name) == FactScope(
                scope_type=SCOPE_TYPE_SEGMENT, scope_name=name
            ).scope_key

    def test_casing_folds_into_one_series_exactly_as_fact_scope_does(self) -> None:
        assert scope_key_for(SCOPE_TYPE_SEGMENT, "SPECIALIST WATCHMAKERS") == (
            scope_key_for(SCOPE_TYPE_SEGMENT, "Specialist Watchmakers")
        )
        assert parse_scope("Specialist Watchmakers").scope_key == scope_key_for(
            SCOPE_TYPE_SEGMENT, "Specialist Watchmakers"
        )

    def test_a_nameless_segment_is_refused(self) -> None:
        with pytest.raises(ValueError, match="needs a name"):
            scope_key_for(SCOPE_TYPE_SEGMENT, "  ")

    def test_region_and_division_are_expressible(self) -> None:
        assert scope_key_for("region", "Greater China") == "region:greater china"
        assert scope_key_for("division", "Watches") == "division:watches"

    def test_an_unrecognised_scope_type_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a recognised reporting scope type"):
            scope_key_for("business_unit", "Watches")


class TestReportingScopes:
    async def test_group_and_a_segment_on_one_entity_are_distinct_rows(
        self, session
    ) -> None:
        # THE CFR shape. Specialist Watchmakers figures must never become Group, and
        # that starts with the two scopes not being the same row.
        cfg = _cfg()
        cfr = await _entity(session, cfg, key="listing:XSWX:CFR", name="Richemont SA")
        group = await upsert_reporting_scope(
            session,
            legal_entity_id=cfr.id,
            payload=ScopeInput(scope_type=SCOPE_TYPE_GROUP, source="annual_report"),
            cfg=cfg,
        )
        watchmakers = await upsert_reporting_scope(
            session,
            legal_entity_id=cfr.id,
            payload=ScopeInput(
                scope_type=SCOPE_TYPE_SEGMENT,
                scope_name="Specialist Watchmakers",
                source="annual_report",
            ),
            cfg=cfg,
        )
        await session.commit()
        assert group is not None and watchmakers is not None
        assert group.id != watchmakers.id
        assert group.scope_key == "group"
        assert watchmakers.scope_key == "segment:specialist watchmakers"
        assert watchmakers.scope_type == SCOPE_TYPE_SEGMENT
        assert group.scope_name is None, "Group has no name of its own"

    async def test_the_same_scope_twice_is_one_row_and_the_span_widens(
        self, session
    ) -> None:
        cfg = _cfg()
        cfr = await _entity(session, cfg, key="listing:XSWX:CFR", name="Richemont SA")
        first = await upsert_reporting_scope(
            session,
            legal_entity_id=cfr.id,
            payload=ScopeInput(
                scope_type=SCOPE_TYPE_SEGMENT,
                scope_name="Specialist Watchmakers",
                period_key="2024",
            ),
            cfg=cfg,
        )
        second = await upsert_reporting_scope(
            session,
            legal_entity_id=cfr.id,
            payload=ScopeInput(
                scope_type=SCOPE_TYPE_SEGMENT,
                scope_name="SPECIALIST WATCHMAKERS",
                period_key="2022",
            ),
            cfg=cfg,
        )
        await session.commit()
        assert first is not None and second is not None and first.id == second.id
        assert second.first_seen_period == "2022"
        assert second.last_seen_period == "2024"

    async def test_two_entities_may_hold_the_same_scope_key(self, session) -> None:
        cfg = _cfg()
        a = await _entity(session, cfg, key="lei:A", name="Alpha S.A.")
        b = await _entity(session, cfg, key="lei:B", name="Beta N.V.")
        for entity in (a, b):
            await upsert_reporting_scope(
                session,
                legal_entity_id=entity.id,
                payload=ScopeInput(scope_type=SCOPE_TYPE_GROUP),
                cfg=cfg,
            )
        await session.commit()
        count = (
            await session.execute(select(func.count()).select_from(ReportingScope))
        ).scalar_one()
        assert count == 2, "scope identity is per issuer, not global"


class TestRenames:
    async def test_a_rename_creates_a_second_scope(self, session) -> None:
        cfg = _cfg()
        cfr = await _entity(session, cfg, key="listing:XSWX:CFR", name="Richemont SA")
        old = await upsert_reporting_scope(
            session,
            legal_entity_id=cfr.id,
            payload=ScopeInput(
                scope_type=SCOPE_TYPE_SEGMENT,
                scope_name="Specialist Watchmakers",
                period_key="2024",
            ),
            cfg=cfg,
        )
        new = await upsert_reporting_scope(
            session,
            legal_entity_id=cfr.id,
            payload=ScopeInput(
                scope_type=SCOPE_TYPE_SEGMENT, scope_name="Watches", period_key="2025"
            ),
            cfg=cfg,
        )
        await session.commit()
        assert old is not None and new is not None and old.id != new.id, (
            "the series splits visibly rather than merging on a guess"
        )

    async def test_linking_a_rename_requires_a_source(self, session) -> None:
        cfg = _cfg()
        cfr = await _entity(session, cfg, key="listing:XSWX:CFR", name="Richemont SA")
        old = await upsert_reporting_scope(
            session,
            legal_entity_id=cfr.id,
            payload=ScopeInput(
                scope_type=SCOPE_TYPE_SEGMENT, scope_name="Specialist Watchmakers"
            ),
            cfg=cfg,
        )
        new = await upsert_reporting_scope(
            session,
            legal_entity_id=cfr.id,
            payload=ScopeInput(scope_type=SCOPE_TYPE_SEGMENT, scope_name="Watches"),
            cfg=cfg,
        )
        assert old is not None and new is not None
        with pytest.raises(ValueError, match="needs a source"):
            await link_scope_rename(
                session,
                successor=new,
                predecessor=old,
                rename_source="  ",
                cfg=cfg,
            )

    async def test_the_database_refuses_an_unsourced_predecessor_too(
        self, session
    ) -> None:
        # Not only the writer: an unsourced merge of two series is not storable even
        # by a direct INSERT.
        cfg = _cfg()
        cfr = await _entity(session, cfg, key="listing:XSWX:CFR", name="Richemont SA")
        old = await upsert_reporting_scope(
            session,
            legal_entity_id=cfr.id,
            payload=ScopeInput(
                scope_type=SCOPE_TYPE_SEGMENT, scope_name="Specialist Watchmakers"
            ),
            cfg=cfg,
        )
        assert old is not None
        await session.commit()
        session.add(
            ReportingScope(
                id=uuid.uuid4(),
                legal_entity_id=cfr.id,
                scope_type=SCOPE_TYPE_SEGMENT,
                scope_name="Watches",
                scope_key="segment:watches",
                predecessor_scope_id=old.id,
                rename_source=None,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async def test_a_sourced_rename_makes_one_lineage(self, session) -> None:
        cfg = _cfg()
        cfr = await _entity(session, cfg, key="listing:XSWX:CFR", name="Richemont SA")
        old = await upsert_reporting_scope(
            session,
            legal_entity_id=cfr.id,
            payload=ScopeInput(
                scope_type=SCOPE_TYPE_SEGMENT, scope_name="Specialist Watchmakers"
            ),
            cfg=cfg,
        )
        new = await upsert_reporting_scope(
            session,
            legal_entity_id=cfr.id,
            payload=ScopeInput(scope_type=SCOPE_TYPE_SEGMENT, scope_name="Watches"),
            cfg=cfg,
        )
        assert old is not None and new is not None
        await link_scope_rename(
            session,
            successor=new,
            predecessor=old,
            rename_source="Annual Report 2025, segment note restatement",
            cfg=cfg,
        )
        await session.commit()

        lineage = await scope_lineage(session, scope=new, cfg=cfg)
        assert [s.scope_name for s in lineage] == ["Watches", "Specialist Watchmakers"]

    async def test_a_rename_across_two_issuers_is_refused(self, session) -> None:
        cfg = _cfg()
        a = await _entity(session, cfg, key="lei:A", name="Alpha S.A.")
        b = await _entity(session, cfg, key="lei:B", name="Beta N.V.")
        old = await upsert_reporting_scope(
            session,
            legal_entity_id=a.id,
            payload=ScopeInput(scope_type=SCOPE_TYPE_SEGMENT, scope_name="Watches"),
            cfg=cfg,
        )
        new = await upsert_reporting_scope(
            session,
            legal_entity_id=b.id,
            payload=ScopeInput(scope_type=SCOPE_TYPE_SEGMENT, scope_name="Clocks"),
            cfg=cfg,
        )
        assert old is not None and new is not None
        with pytest.raises(ValueError, match="within one issuer"):
            await link_scope_rename(
                session, successor=new, predecessor=old, rename_source="x", cfg=cfg
            )

    async def test_a_predecessor_cycle_terminates(self, session) -> None:
        cfg = _cfg()
        a = await _entity(session, cfg, key="lei:A", name="Alpha S.A.")
        first = await upsert_reporting_scope(
            session,
            legal_entity_id=a.id,
            payload=ScopeInput(scope_type=SCOPE_TYPE_SEGMENT, scope_name="One"),
            cfg=cfg,
        )
        second = await upsert_reporting_scope(
            session,
            legal_entity_id=a.id,
            payload=ScopeInput(scope_type=SCOPE_TYPE_SEGMENT, scope_name="Two"),
            cfg=cfg,
        )
        assert first is not None and second is not None
        await link_scope_rename(
            session, successor=second, predecessor=first, rename_source="s", cfg=cfg
        )
        await link_scope_rename(
            session, successor=first, predecessor=second, rename_source="s", cfg=cfg
        )
        await session.commit()
        lineage = await scope_lineage(session, scope=second, cfg=cfg)
        assert len(lineage) == 2, "following a cycle forever is worse than a short answer"


class TestSegmentDisclosures:
    async def test_two_periods_of_one_scope_are_two_rows_and_one_series(
        self, session
    ) -> None:
        cfg = _cfg()
        cfr = await _entity(session, cfg, key="listing:XSWX:CFR", name="Richemont SA")
        scope = await upsert_reporting_scope(
            session,
            legal_entity_id=cfr.id,
            payload=ScopeInput(
                scope_type=SCOPE_TYPE_SEGMENT, scope_name="Specialist Watchmakers"
            ),
            cfg=cfg,
        )
        assert scope is not None
        for period in ("2024", "2025"):
            await record_segment_disclosure(
                session,
                reporting_scope=scope,
                reported_name="Specialist Watchmakers",
                period_key=period,
                cfg=cfg,
                source="annual_report",
            )
        await session.commit()

        series = await segment_series(session, reporting_scope_id=scope.id, cfg=cfg)
        assert [s.period_key for s in series] == ["2024", "2025"]
        assert all(s.legal_entity_id == cfr.id for s in series)

    async def test_the_reported_name_is_stored_verbatim(self, session) -> None:
        # The exact wording is what a citation has to be able to quote; normalising it
        # away would make the quote a paraphrase.
        cfg = _cfg()
        cfr = await _entity(session, cfg, key="listing:XSWX:CFR", name="Richemont SA")
        scope = await upsert_reporting_scope(
            session,
            legal_entity_id=cfr.id,
            payload=ScopeInput(
                scope_type=SCOPE_TYPE_SEGMENT, scope_name="Jewellery Maisons"
            ),
            cfg=cfg,
        )
        assert scope is not None
        row = await record_segment_disclosure(
            session,
            reporting_scope=scope,
            reported_name="Jewellery Maisons",
            period_key="2025",
            cfg=cfg,
        )
        await session.commit()
        assert row is not None
        assert row.reported_name == "Jewellery Maisons"
        assert row.normalized_name == "jewellery maisons"

    async def test_re_recording_one_period_updates_rather_than_duplicating(
        self, session
    ) -> None:
        cfg = _cfg()
        a = await _entity(session, cfg, key="lei:A", name="Alpha S.A.")
        scope = await upsert_reporting_scope(
            session,
            legal_entity_id=a.id,
            payload=ScopeInput(scope_type=SCOPE_TYPE_SEGMENT, scope_name="Watches"),
            cfg=cfg,
        )
        assert scope is not None
        for name in ("Watches", "Watches "):
            await record_segment_disclosure(
                session,
                reporting_scope=scope,
                reported_name=name,
                period_key="2025",
                cfg=cfg,
            )
        await session.commit()
        count = (
            await session.execute(select(func.count()).select_from(BusinessSegment))
        ).scalar_one()
        assert count == 1

    async def test_a_disclosure_with_no_period_is_refused(self, session) -> None:
        cfg = _cfg()
        a = await _entity(session, cfg, key="lei:A", name="Alpha S.A.")
        scope = await upsert_reporting_scope(
            session,
            legal_entity_id=a.id,
            payload=ScopeInput(scope_type=SCOPE_TYPE_SEGMENT, scope_name="Watches"),
            cfg=cfg,
        )
        assert scope is not None
        with pytest.raises(ValueError, match="needs a period"):
            await record_segment_disclosure(
                session,
                reporting_scope=scope,
                reported_name="Watches",
                period_key="  ",
                cfg=cfg,
            )


# --------------------------------------------------------------------------- #
# The ADR ratio — the reason it is stored at all
# --------------------------------------------------------------------------- #


class TestPerShareRefusal:
    async def test_an_ordinary_share_is_one_to_one(self, session) -> None:
        cfg = _cfg()
        a = await _entity(session, cfg, key="lei:A", name="Alpha S.A.")
        ordinary = await upsert_security(
            session,
            legal_entity_id=a.id,
            security_key="ordinary_share:1",
            cfg=cfg,
            security_type=SECURITY_ORDINARY_SHARE,
        )
        assert ordinary is not None
        assert underlying_shares_per_unit(ordinary) == Decimal(1)

    async def test_a_receipt_with_no_ratio_makes_arithmetic_refuse(
        self, session
    ) -> None:
        # Assuming 1:1 silently reports earnings-per-receipt as earnings-per-share.
        # For a receipt over four underlying shares that is a 4x error in a number a
        # human will act on.
        cfg = _cfg()
        a = await _entity(session, cfg, key="lei:A", name="Alpha S.A.")
        adr = await upsert_security(
            session,
            legal_entity_id=a.id,
            security_key="adr:1",
            cfg=cfg,
            security_type=SECURITY_ADR,
        )
        assert adr is not None and adr.receipt_ratio is None
        with pytest.raises(PerShareNotPermittedError, match="must not assume 1:1"):
            underlying_shares_per_unit(adr)

    async def test_the_ratio_direction_is_underlying_shares_per_one_unit(
        self, session
    ) -> None:
        # A bare "ratio" of 4 could mean four receipts per share or four shares per
        # receipt, and a reader who guesses wrong is out by 16x. Both directions are
        # pinned so the meaning cannot drift into a comment.
        cfg = _cfg()
        a = await _entity(session, cfg, key="lei:A", name="Alpha S.A.")
        four_shares = await upsert_security(
            session,
            legal_entity_id=a.id,
            security_key="adr:4",
            cfg=cfg,
            security_type=SECURITY_ADR,
        )
        quarter_share = await upsert_security(
            session,
            legal_entity_id=a.id,
            security_key="adr:q",
            cfg=cfg,
            security_type=SECURITY_ADR,
        )
        assert four_shares is not None and quarter_share is not None
        four_shares.receipt_ratio = Decimal("4")
        quarter_share.receipt_ratio = Decimal("0.25")
        await session.commit()

        # per_unit = per_share * multiplier
        per_share_eps = Decimal("2.00")
        assert underlying_shares_per_unit(four_shares) == Decimal("4")
        assert per_share_eps * underlying_shares_per_unit(four_shares) == Decimal("8.00")
        assert underlying_shares_per_unit(quarter_share) == Decimal("0.25")
        assert per_share_eps * underlying_shares_per_unit(quarter_share) == Decimal(
            "0.50"
        )

    async def test_a_receipt_with_a_ratio_returns_it(self, session) -> None:
        cfg = _cfg()
        a = await _entity(session, cfg, key="lei:A", name="Alpha S.A.")
        ordinary = await upsert_security(
            session, legal_entity_id=a.id, security_key="ordinary_share:1", cfg=cfg
        )
        adr = await upsert_security(
            session,
            legal_entity_id=a.id,
            security_key="adr:1",
            cfg=cfg,
            security_type=SECURITY_ADR,
        )
        assert ordinary is not None and adr is not None
        adr.underlying_security_id = ordinary.id
        adr.receipt_ratio = Decimal("0.25")
        await session.commit()

        assert underlying_shares_per_unit(adr) == Decimal("0.25")
        assert adr.underlying_security_id == ordinary.id

    async def test_a_non_positive_ratio_is_refused_by_the_schema(self, session) -> None:
        cfg = _cfg()
        a = await _entity(session, cfg, key="lei:A", name="Alpha S.A.")
        adr = await upsert_security(
            session,
            legal_entity_id=a.id,
            security_key="adr:1",
            cfg=cfg,
            security_type=SECURITY_ADR,
        )
        assert adr is not None
        adr.receipt_ratio = Decimal("0")
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


class TestDisabledByDefault:
    async def test_with_the_flag_off_nothing_is_written(self, session) -> None:
        off = _cfg(v3_entity_master_enabled=False)
        assert (
            await record_relationship(
                session,
                subject_entity_id=uuid.uuid4(),
                object_entity_id=uuid.uuid4(),
                relationship_type=REL_PARENT_OF,
                source="x",
                cfg=off,
            )
            is None
        )
        assert (
            await upsert_reporting_scope(
                session,
                legal_entity_id=uuid.uuid4(),
                payload=ScopeInput(scope_type=SCOPE_TYPE_GROUP),
                cfg=off,
            )
            is None
        )
        assert (
            await relationships_for(session, legal_entity_id=uuid.uuid4(), cfg=off) == []
        )
        assert (
            await segment_series(session, reporting_scope_id=uuid.uuid4(), cfg=off) == []
        )
        await session.commit()
        for model in (EntityRelationship, ReportingScope, BusinessSegment):
            count = (
                await session.execute(select(func.count()).select_from(model))
            ).scalar_one()
            assert count == 0

    async def test_the_new_columns_are_nullable_and_securities_still_works(
        self, session
    ) -> None:
        # Additive only: a security written without either new column is valid, which
        # is what lets release/v2-current run against a migrated database.
        cfg = _cfg()
        a = await _entity(session, cfg, key="lei:A", name="Alpha S.A.")
        plain = await upsert_security(
            session, legal_entity_id=a.id, security_key="ordinary_share:1", cfg=cfg
        )
        await session.commit()
        assert plain is not None
        assert plain.underlying_security_id is None and plain.receipt_ratio is None
        for name in ("underlying_security_id", "receipt_ratio"):
            assert Security.__table__.columns[name].nullable is True
