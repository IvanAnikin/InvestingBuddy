"""Open-web W4 on real PostgreSQL — the ledger's claim-rule and contradiction path.

Set ``V3_TEST_POSTGRES_URL`` to a database at head (044). Each test rolls back.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.ledger import ResearchFinding, ResearchGap
from app.services import research_fields as rf
from app.services.ledger import store as ledger
from app.services.web_research import trust

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
requires_postgres = pytest.mark.skipif(
    not POSTGRES_URL, reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head 044"
)

ISSUER_KEY = "acme"
ISSUER_ORIGIN = f"issuer:{ISSUER_KEY}"


def _web(eid: str, klass: str, origin: str) -> trust.SupportItem:
    return trust.SupportItem(evidence_id=eid, source_class=klass, origin_key=origin, web=True)


async def _record(session, run, statement: str, evidence: list[str], support, **kw):  # noqa: ANN001, ANN003, ANN202
    return await ledger.record_finding(
        session, run, statement=statement, evidence_ids=evidence,
        claim_key=rf.claim_key_for(statement), period_key="FY2025", support=support,
        issuer_key=ISSUER_KEY, **kw,
    )


@requires_postgres
class TestLedgerTrustPathOnPostgres:
    async def _maker(self):  # noqa: ANN202
        engine = create_async_engine(POSTGRES_URL, future=True)
        return engine, async_sessionmaker(engine, expire_on_commit=False)

    async def test_web_revenue_is_context_and_the_conflict_is_recorded(self) -> None:
        engine, maker = await self._maker()
        try:
            async with maker() as session:
                run = await ledger.open_run(session, mode="standard")
                filing = await _record(
                    session, run, "Revenue for FY2025 was US$1.20 billion.", ["fact-1"],
                    [trust.SupportItem("fact-1", "issuer_filing", ISSUER_ORIGIN)],
                    question_key="revenue",
                )
                web = await _record(
                    session, run, "Revenue for FY2025 was US$1.50 billion.", ["ev:x:press-1"],
                    [_web("ev:x:press-1", "major_financial_press", "reuters.com")],
                    question_key="revenue",
                )
                await session.flush()
                rows = (
                    await session.execute(
                        select(ResearchFinding).where(ResearchFinding.research_run_id == run.id)
                    )
                ).scalars().all()
                assert len(rows) == 2  # both kept
                assert filing.statement == "Revenue for FY2025 was US$1.20 billion."
                # Value-free: no second money figure rides inside the statement (H3).
                assert web.statement.startswith(
                    "[reported in the press; a filing figure differs] "
                )
                assert "1.20" not in web.statement
                outcome = web._trust_outcome  # type: ignore[attr-defined]
                assert outcome.web_fact.kind == trust.WEB_FACT_REPORTED_VALUE
                assert outcome.contradictions == ["metric:revenue"]
                gaps = (
                    await session.execute(
                        select(ResearchGap).where(
                            ResearchGap.research_run_id == run.id,
                            ResearchGap.gap_type == ledger.GAP_CONFLICTING_SOURCES,
                        )
                    )
                ).scalars().all()
                assert len(gaps) == 1
                assert "filing value is canonical" in gaps[0].description
                assert str(gaps[0].id) in outcome.conflict_gap_ids
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_conflicting_web_sources_are_both_retained(self) -> None:
        engine, maker = await self._maker()
        try:
            async with maker() as session:
                run = await ledger.open_run(session, mode="standard")
                a = await _record(
                    session, run, "Net debt for FY2025 was US$300 million.", ["ev:x:a"],
                    [_web("ev:x:a", "major_financial_press", "reuters.com")],
                )
                b = await _record(
                    session, run, "Net debt for FY2025 was US$450 million.", ["ev:x:b"],
                    [_web("ev:x:b", "trade_publication", "mining.example")],
                )
                gaps = (
                    await session.execute(
                        select(ResearchGap).where(
                            ResearchGap.research_run_id == run.id,
                            ResearchGap.gap_type == ledger.GAP_CONFLICTING_SOURCES,
                        )
                    )
                ).scalars().all()
                assert len(gaps) == 1 and "No automatic winner" in gaps[0].description
                # No winner: neither is relabelled with the other's value.
                assert "the filing says" not in a.statement
                assert "the filing says" not in b.statement
                assert (await session.get(ResearchFinding, a.id)) is not None
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_an_issuer_only_superlative_is_stored_labelled(self) -> None:
        engine, maker = await self._maker()
        try:
            async with maker() as session:
                run = await ledger.open_run(session, mode="standard")
                statement = "Acme is the world's largest producer of battery-grade lithium."
                row = await _record(
                    session, run, statement, ["ev:c:pr"],
                    [_web("ev:c:pr", "company_press_release", ISSUER_ORIGIN)],
                )
                await session.flush()
                stored = (
                    await session.execute(
                        select(ResearchFinding.statement).where(ResearchFinding.id == row.id)
                    )
                ).scalar_one()
                assert stored == f"[company describes itself as …] {statement}"
                # A platform-only finding stating the same is untouched (W4 is inert
                # without web evidence).
                plain = await _record(
                    session, run, statement, ["fact-2"],
                    [trust.SupportItem("fact-2", "issuer_filing", ISSUER_ORIGIN)],
                )
                assert plain.statement == statement
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_an_error_in_the_contradiction_scan_costs_neither_the_finding_nor_the_txn(
        self, monkeypatch
    ) -> None:
        from sqlalchemy import text

        engine, maker = await self._maker()
        try:
            async with maker() as session:
                run = await ledger.open_run(session, mode="standard")
                await _record(
                    session, run, "Revenue for FY2025 was US$1.20 billion.", ["fact-1"],
                    [trust.SupportItem("fact-1", "issuer_filing", ISSUER_ORIGIN)],
                )

                async def broken(sess, ids, **_kw):  # noqa: ANN001, ANN003, ANN202
                    await sess.execute(text("select 1/0"))  # a REAL database error

                monkeypatch.setattr(trust, "resolve_support", broken)
                web = await _record(
                    session, run, "Revenue for FY2025 was US$1.50 billion.", ["ev:x:p"],
                    [_web("ev:x:p", "major_financial_press", "reuters.com")],
                )
                # The finding is stored, labelled by the claim rule, and the caller's
                # objects are usable (no MissingGreenlet / aborted transaction).
                assert web.statement.startswith("[reported in the press; not from a filing] ")
                assert web.id is not None and run.id is not None
                monkeypatch.undo()
                after = await _record(
                    session, run, "Revenue for FY2025 was US$1.60 billion.", ["ev:x:q"],
                    [_web("ev:x:q", "trade_publication", "mining.example")],
                )
                await session.flush()
                assert after.id is not None
                await session.rollback()
        finally:
            await engine.dispose()

    async def test_the_scan_cost_does_not_grow_with_the_number_of_findings(self) -> None:
        from sqlalchemy import event

        engine, maker = await self._maker()
        statements: list[str] = []

        def _count(conn, cursor, statement, *_rest):  # noqa: ANN001, ANN202
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", _count)
        try:
            counts = []
            for prior in (4, 30):
                async with maker() as session:
                    run = await ledger.open_run(session, mode="standard")
                    for index in range(prior):
                        await _record(
                            session, run, f"Revenue for FY2025 was US${index + 2}.00 billion.",
                            [f"fact-{index}"],
                            [trust.SupportItem(f"fact-{index}", "issuer_filing", ISSUER_ORIGIN)],
                        )
                    await session.flush()
                    statements.clear()
                    await _record(
                        session, run, "Revenue for FY2025 was US$1.50 billion.", ["ev:x:p"],
                        [_web("ev:x:p", "major_financial_press", "reuters.com")],
                    )
                    await session.flush()
                    counts.append(len(statements))
                    await session.rollback()
            # The same number of statements for 4 and 30 prior findings (the scan, ONE
            # batched support lookup, and the writes) — not one lookup per finding.
            assert counts[0] == counts[1], counts
            assert counts[1] <= 14, counts
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _count)
            await engine.dispose()


@requires_postgres
class TestNearDuplicatePrefilterOnPostgres:
    async def test_the_sql_band_extraction_agrees_with_python_for_signed_values(self) -> None:
        import random

        import sqlalchemy as sa
        from sqlalchemy import literal_column

        from app.services.web_research import dedup

        engine = create_async_engine(POSTGRES_URL, future=True)
        try:
            rnd = random.Random(3)
            values = [0, -1, (1 << 63) - 1, -(1 << 63)] + [
                rnd.getrandbits(64) - (1 << 63) for _ in range(20)
            ]
            async with engine.connect() as conn:
                for value in values:
                    column = sa.literal(value, sa.BigInteger)
                    bands = []
                    for i in range(dedup._BANDS):  # noqa: SLF001 - the very expression ingest uses
                        expr = column.op(">>")(literal_column(str(i * dedup._BAND_BITS))).op(
                            "&")(literal_column(str((1 << dedup._BAND_BITS) - 1)))
                        bands.append((await conn.execute(sa.select(expr))).scalar_one())
                    assert bands == [b for _i, b in dedup._bands(value)], value  # noqa: SLF001
        finally:
            await engine.dispose()

    async def test_a_scoped_lookup_runs_on_postgres(self) -> None:
        from app.services.web_research import dedup

        engine, maker = (
            create_async_engine(POSTGRES_URL, future=True), None
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as session:
                # No scope means no candidates, and a scoped query is valid SQL on PG.
                assert await dedup.near_duplicate_rows(session, 12345) == []
                import uuid

                assert await dedup.near_duplicate_rows(
                    session, 12345, company_id=uuid.uuid4()
                ) == []
                assert await dedup.near_duplicate_rows(
                    session, 12345, theme_key="lithium"
                ) == []
        finally:
            await engine.dispose()
