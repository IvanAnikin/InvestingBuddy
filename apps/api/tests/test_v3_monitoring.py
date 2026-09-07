"""V3.9 Slice 9.1 — watchlists, change detection and signals.

TWO THINGS THIS PHASE MUST NOT DO
=================================
1. **Schedule itself.** How often to check is OPEN DECISION #15, user-owned and
   unresolved. Guessing a cadence would answer a question asked of somebody else *and*
   start spending on a path nobody approved.
2. **Become a feed.** "Pandora filed something" is a feed. An issuer with no prior
   research therefore produces **no signals**: there is nothing for a change to be
   relative to.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models.company import Company
from app.models.monitoring import MonitoringSignal, Watchlist, WatchlistEntry
from app.models.research_document import ResearchDocument, ResearchDocumentVersion
from app.services.ir_events import service as ir
from app.services.ledger import store as ledger
from app.services.monitoring.detector import (
    KIND_NEW_DOCUMENT,
    KIND_NEW_IR_EVENT,
    KIND_TRANSCRIPT_PUBLISHED,
    SIGNAL_KINDS,
    STATUS_OPEN,
    DetectedSignal,
    acknowledge,
    detect_for_company,
    open_signals,
    persist_signals,
    signal_key_for,
    watchlist_company_ids,
)

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)
LAST_RUN = NOW - timedelta(days=30)


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _cfg(**overrides) -> Settings:
    base = {"v3_monitoring_enabled": True}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


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


async def _company(session) -> Company:  # noqa: ANN001
    company = Company(
        id=uuid.uuid4(), ticker="PNDORA", exchange="CO", name="Pandora", status="new"
    )
    session.add(company)
    await session.flush()
    return company


async def _prior_run(session, company):  # noqa: ANN001
    run = await ledger.open_run(
        session, mode="standard", company_id=company.id, now=LAST_RUN
    )
    await ledger.record_finding(
        session, run, statement="A conclusion.", evidence_ids=["ev:a"]
    )
    await ledger.close_run(
        session, run, status=ledger.RUN_COMPLETE, now=LAST_RUN
    )
    return run


async def _document(session, company, *, retrieved: datetime, content_hash: str):  # noqa: ANN001
    document = ResearchDocument(
        id=uuid.uuid4(),
        company_id=company.id,
        document_key=f"doc:{content_hash[:10]}",
        document_type="annual_report",
        first_seen_at=retrieved,
        last_seen_at=retrieved,
    )
    session.add(document)
    await session.flush()
    version = ResearchDocumentVersion(
        id=uuid.uuid4(),
        research_document_id=document.id,
        content_hash=content_hash,
        canonical_url=f"https://issuer.example/{content_hash[:8]}.pdf",
        transport="company_ir",
        source_tier="T1_primary_filing",
        access_class="public_issuer",
        retrieved_at=retrieved,
        extraction_status="extracted",
        is_current=True,
    )
    session.add(version)
    await session.flush()
    return version


class TestNothingSchedulesIt:
    def test_no_scheduler_references_the_detector(self) -> None:
        """OPEN DECISION #15 is user-owned. Guessing a cadence would answer a question
        asked of somebody else and start spending on a path nobody approved."""
        from pathlib import Path

        root = Path("app")
        offenders: list[str] = []
        for path in root.rglob("*.py"):
            if "monitoring" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if "monitoring.detector" in text or "detect_for_company" in text:
                offenders.append(str(path))
        assert offenders == [], f"something outside the package calls it: {offenders}"

    def test_the_detector_imports_and_calls_no_scheduler(self) -> None:
        """Checked on the AST, not on the text: the module's own docstring explains that
        there is no cron, and a substring search would flag the explanation."""
        import ast
        from pathlib import Path

        tree = ast.parse(
            Path("app/services/monitoring/detector.py").read_text(encoding="utf-8")
        )
        imported: set[str] = set()
        called: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    called.add(node.func.attr)
                elif isinstance(node.func, ast.Name):
                    called.add(node.func.id)
        assert not (imported & {"apscheduler", "celery", "schedule", "crontab"})
        assert not (called & {"sleep", "add_job", "every", "delay", "apply_async"})

    async def test_with_the_flag_off_nothing_is_observed_and_it_says_so(
        self, session
    ) -> None:
        company = await _company(session)
        await _prior_run(session, company)
        await session.commit()
        result = await detect_for_company(
            session, company_id=company.id, cfg=Settings(), now=NOW
        )
        assert result.signals == []
        assert "V3_MONITORING_ENABLED is off" in (result.reason or "")


class TestNotAFeed:
    async def test_an_issuer_with_no_prior_research_produces_no_signals(
        self, session
    ) -> None:
        """There is nothing for a change to be relative to, and raising one anyway would
        make this a feed."""
        company = await _company(session)
        await _document(session, company, retrieved=NOW, content_hash="a" * 64)
        await session.commit()
        result = await detect_for_company(
            session, company_id=company.id, cfg=_cfg(), now=NOW
        )
        assert result.signals == []
        assert "nothing for a change to be relative to" in (result.reason or "")

    async def test_a_document_from_before_the_last_run_is_not_new(
        self, session
    ) -> None:
        company = await _company(session)
        await _prior_run(session, company)
        await _document(
            session, company, retrieved=LAST_RUN - timedelta(days=5), content_hash="b" * 64
        )
        await session.commit()
        result = await detect_for_company(
            session, company_id=company.id, cfg=_cfg(), now=NOW
        )
        assert result.signals == []
        assert "nothing observable has changed" in (result.reason or "")

    async def test_a_document_retrieved_since_the_last_run_is_a_signal(
        self, session
    ) -> None:
        company = await _company(session)
        run = await _prior_run(session, company)
        await _document(
            session, company, retrieved=NOW - timedelta(days=1), content_hash="c" * 64
        )
        await session.commit()
        result = await detect_for_company(
            session, company_id=company.id, cfg=_cfg(), now=NOW
        )
        assert [s.kind for s in result.signals] == [KIND_NEW_DOCUMENT]
        assert result.signals[0].relates_to_run_id == run.id

    async def test_a_new_ir_event_is_a_signal(self, session) -> None:
        company = await _company(session)
        await _prior_run(session, company)
        await ir.upsert_event(
            session,
            ir.EventSpec(
                issuer_key="pndora:co",
                event_type=ir.EVENT_EARNINGS_CALL,
                status=ir.STATUS_OCCURRED,
                fiscal_period_key="2026",
                occurred_at=NOW - timedelta(days=2),
            ),
            company_id=company.id,
        )
        await session.commit()
        result = await detect_for_company(
            session, company_id=company.id, cfg=_cfg(), now=NOW
        )
        assert KIND_NEW_IR_EVENT in {s.kind for s in result.signals}

    async def test_a_transcript_that_has_since_appeared_is_the_signal_that_matters(
        self, session
    ) -> None:
        """An event previously recorded `not_published` whose transcript now exists is a
        gap the last run could not close and this run can."""
        company = await _company(session)
        await _prior_run(session, company)
        event = await ir.upsert_event(
            session,
            ir.EventSpec(
                issuer_key="pndora:co",
                event_type=ir.EVENT_EARNINGS_CALL,
                status=ir.STATUS_OCCURRED,
                fiscal_period_key="2025",
                occurred_at=LAST_RUN - timedelta(days=10),
            ),
            company_id=company.id,
        )
        event.first_seen_at = LAST_RUN - timedelta(days=10)
        await ir.record_availability(
            session,
            event,
            material_type=ir.MATERIAL_TRANSCRIPT,
            availability=ir.AVAILABILITY_AVAILABLE,
            url="https://issuer.example/t.pdf",
            checked_at=NOW - timedelta(days=1),
        )
        await session.commit()
        result = await detect_for_company(
            session, company_id=company.id, cfg=_cfg(), now=NOW
        )
        kinds = {s.kind for s in result.signals}
        assert KIND_TRANSCRIPT_PUBLISHED in kinds
        # And the EVENT is not re-raised as new: it was already on record.
        assert KIND_NEW_IR_EVENT not in kinds


class TestSignalsAreObservationsNotConclusions:
    def test_an_invented_kind_is_refused(self) -> None:
        with pytest.raises(ValueError, match="filter a watchlist on"):
            DetectedSignal(kind="feels_important", signal_key="k", summary="x")

    def test_the_kind_vocabulary_is_closed_at_six(self) -> None:
        assert len(SIGNAL_KINDS) == 6

    async def test_a_signal_summary_says_what_changed_not_what_it_means(
        self, session
    ) -> None:
        company = await _company(session)
        await _prior_run(session, company)
        await _document(
            session, company, retrieved=NOW - timedelta(days=1), content_hash="d" * 64
        )
        await session.commit()
        result = await detect_for_company(
            session, company_id=company.id, cfg=_cfg(), now=NOW
        )
        summary = result.signals[0].summary.lower()
        for verdict in ("bullish", "bearish", "buy", "sell", "positive", "negative"):
            assert verdict not in summary


class TestDeduplication:
    def test_the_key_is_derived_from_what_was_observed(self) -> None:
        """A key containing a timestamp would make every pass produce new signals, which
        is exactly the behaviour being prevented."""
        a = signal_key_for(KIND_NEW_DOCUMENT, "company", "hash")
        b = signal_key_for(KIND_NEW_DOCUMENT, "company", "hash")
        assert a == b
        assert a != signal_key_for(KIND_NEW_DOCUMENT, "company", "other")

    async def test_a_repeated_pass_raises_nothing_twice(self, session) -> None:
        company = await _company(session)
        await _prior_run(session, company)
        await _document(
            session, company, retrieved=NOW - timedelta(days=1), content_hash="e" * 64
        )
        await session.commit()
        first = await persist_signals(
            session,
            await detect_for_company(session, company_id=company.id, cfg=_cfg(), now=NOW),
            now=NOW,
        )
        await session.commit()
        second = await persist_signals(
            session,
            await detect_for_company(session, company_id=company.id, cfg=_cfg(), now=NOW),
            now=NOW,
        )
        await session.commit()
        assert first.persisted == 1
        assert second.persisted == 0
        assert second.duplicates_suppressed == 1

    async def test_the_database_refuses_a_second_open_signal_for_one_key(
        self, session
    ) -> None:
        """Checked in Python and enforced by a partial unique index, because the second
        is what holds when two passes run at once."""
        company = await _company(session)
        await session.commit()
        for _ in range(2):
            session.add(
                MonitoringSignal(
                    id=uuid.uuid4(),
                    company_id=company.id,
                    kind=KIND_NEW_DOCUMENT,
                    signal_key="same-key",
                    summary="x",
                    status=STATUS_OPEN,
                    observed_at=NOW,
                )
            )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async def test_acknowledging_lets_the_same_key_be_raised_again(
        self, session
    ) -> None:
        """History, not suppression forever. "We told you in March and again in June" is
        answerable; "we told you eleven times in March" is impossible."""
        company = await _company(session)
        await session.commit()
        first = MonitoringSignal(
            id=uuid.uuid4(),
            company_id=company.id,
            kind=KIND_NEW_DOCUMENT,
            signal_key="k",
            summary="x",
            status=STATUS_OPEN,
            observed_at=NOW,
        )
        session.add(first)
        await session.commit()
        await acknowledge(session, first, now=NOW)
        session.add(
            MonitoringSignal(
                id=uuid.uuid4(),
                company_id=company.id,
                kind=KIND_NEW_DOCUMENT,
                signal_key="k",
                summary="it changed again",
                status=STATUS_OPEN,
                observed_at=NOW,
            )
        )
        await session.commit()
        rows = (await session.execute(select(MonitoringSignal))).scalars().all()
        assert len(rows) == 2
        assert first.acknowledged_at is not None

    async def test_there_is_no_dismissed_status(self) -> None:
        """A signal somebody judged unimportant is *acknowledged*; one the world overtook
        is *superseded*. Neither is a deletion."""
        from app.services.monitoring.detector import SIGNAL_STATUSES

        assert "dismissed" not in SIGNAL_STATUSES
        assert "deleted" not in SIGNAL_STATUSES


class TestWatchlists:
    async def test_a_watchlist_carries_its_issuers(self, session) -> None:
        company = await _company(session)
        watchlist = Watchlist(id=uuid.uuid4(), name="Luxury", is_active=True)
        session.add(watchlist)
        await session.flush()
        session.add(
            WatchlistEntry(
                id=uuid.uuid4(),
                watchlist_id=watchlist.id,
                company_id=company.id,
                added_at=NOW,
            )
        )
        await session.commit()
        assert await watchlist_company_ids(session, watchlist) == [company.id]

    async def test_one_issuer_cannot_be_on_one_watchlist_twice(self, session) -> None:
        company = await _company(session)
        watchlist = Watchlist(id=uuid.uuid4(), name="Luxury", is_active=True)
        session.add(watchlist)
        await session.flush()
        for _ in range(2):
            session.add(
                WatchlistEntry(
                    id=uuid.uuid4(),
                    watchlist_id=watchlist.id,
                    company_id=company.id,
                    added_at=NOW,
                )
            )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async def test_a_watchlist_has_an_owner_column_from_day_one(self) -> None:
        """Retrofitting a tenant boundary onto a store that never had an owner column is
        a rewrite; adding it now is a nullable field."""
        assert "owner" in Watchlist.__table__.columns

    async def test_open_signals_are_filterable_by_issuer_and_kind(
        self, session
    ) -> None:
        company = await _company(session)
        await session.commit()
        for kind, key in ((KIND_NEW_DOCUMENT, "a"), (KIND_NEW_IR_EVENT, "b")):
            session.add(
                MonitoringSignal(
                    id=uuid.uuid4(),
                    company_id=company.id,
                    kind=kind,
                    signal_key=key,
                    summary="x",
                    status=STATUS_OPEN,
                    observed_at=NOW,
                )
            )
        await session.commit()
        found = await open_signals(
            session, company_id=company.id, kinds=[KIND_NEW_IR_EVENT]
        )
        assert [s.kind for s in found] == [KIND_NEW_IR_EVENT]
