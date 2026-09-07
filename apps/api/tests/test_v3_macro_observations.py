"""V3.4 Slice 4.7 — the macro observation store, its first live source, and the tool.

THE ONE THING THESE TESTS ARE REALLY ABOUT
==========================================
Vintages. A macro series is revised, and a store that overwrote the previous reading
would silently change the macro context of every report that cited it — months after the
report was signed off. Almost everything below is a statement about that.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models.macro import MacroObservation
from app.services.agent_tools.macro import (
    GET_MACRO_SERIES_SPEC,
    validate_get_macro_series,
)
from app.services.macro import store as macro_store
from app.services.macro.definitions import (
    FREQ_ANNUAL,
    FREQ_MONTHLY,
    FREQ_QUARTERLY,
    DatasetSpec,
    SeriesSpec,
    UnknownMacroPeriodError,
    parse_macro_period,
)
from app.services.macro.sources import (
    FETCH_NO_DATA,
    FETCH_NOT_CONFIGURED,
    FETCH_OK,
    FETCH_UNPARSEABLE,
    FETCH_UNREACHABLE,
    SeriesFetch,
    StaticMacroSource,
    parse_world_bank_payload,
)
from app.services.macro.world_bank import WorldBankMacroSource, build_url

JULY = datetime(2026, 7, 15, tzinfo=timezone.utc)
OCTOBER = datetime(2026, 10, 15, tzinfo=timezone.utc)
AUGUST = datetime(2026, 8, 1, tzinfo=timezone.utc)


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


_DATASET = DatasetSpec(
    dataset_key="world_bank:wdi",
    source_id="world_bank_indicators",
    display_name="World Development Indicators",
)
_SERIES = SeriesSpec(
    series_key="NY.GDP.MKTP.CD:DNK",
    display_name="GDP (current US$) — DNK",
    unit="USD",
    frequency=FREQ_ANNUAL,
    geography="DNK",
)


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


async def _series(session):  # noqa: ANN001
    dataset = await macro_store.upsert_dataset(session, _DATASET)
    return await macro_store.upsert_series(session, dataset, _SERIES)


# --------------------------------------------------------------------------- #
# Periods
# --------------------------------------------------------------------------- #


class TestMacroPeriods:
    def test_the_four_shapes_parse_with_their_bounds(self) -> None:
        assert parse_macro_period("2025").frequency == FREQ_ANNUAL
        q = parse_macro_period("2025-Q2")
        assert (q.frequency, str(q.start), str(q.end)) == (
            FREQ_QUARTERLY,
            "2025-04-01",
            "2025-06-30",
        )
        m = parse_macro_period("2025-02")
        assert (m.frequency, str(m.end)) == (FREQ_MONTHLY, "2025-02-28")

    def test_a_leap_february_ends_on_the_29th(self) -> None:
        assert str(parse_macro_period("2024-02").end) == "2024-02-29"

    def test_an_unrecognised_key_raises_rather_than_becoming_unknown(self) -> None:
        """The opposite of `financial_period.parse_period`, deliberately.

        A financial period comes from a DOCUMENT, where "not stated" is a real state. A
        macro period key was built by a connector from a publisher's structured
        response, so an unrecognised one is a defect in the connector — and storing it
        would put a row in the series that nothing can order.
        """
        with pytest.raises(UnknownMacroPeriodError):
            parse_macro_period("2025-H1")
        with pytest.raises(UnknownMacroPeriodError):
            parse_macro_period("FY2025")

    def test_neither_vocabulary_accepts_the_others_exclusive_keys(self) -> None:
        """The two are separate on purpose, and each refuses what only the other means.

        A fiscal split year (``2025/26``) and a fiscal half (``H1 2025``) have no
        statistical analogue; a statistical month (``2025-07``) has no fiscal one.
        Richemont's FY2025 ends in March, so a shared vocabulary would invite placing a
        calendar 2025 macro reading beside it as "the same year".
        """
        from app.services.sources.financial_period import parse_period

        for fiscal_only in ("2025/26", "H1 2025"):
            assert not parse_period(fiscal_only).is_unknown
            with pytest.raises(UnknownMacroPeriodError):
                parse_macro_period(fiscal_only)

        # And the reverse: a statistical month is unknown to the fiscal vocabulary.
        assert parse_period("2025-07").is_unknown
        assert parse_macro_period("2025-07").frequency == FREQ_MONTHLY

    def test_a_series_needs_a_unit(self) -> None:
        with pytest.raises(ValueError, match="needs a unit"):
            SeriesSpec(
                series_key="x", display_name="x", unit="  ", frequency=FREQ_ANNUAL
            )

    def test_not_stated_is_not_not_adjusted(self) -> None:
        assert SeriesSpec(
            series_key="x", display_name="x", unit="percent", frequency=FREQ_MONTHLY
        ).seasonal_adjustment is None
        with pytest.raises(ValueError, match="never the same as"):
            SeriesSpec(
                series_key="x",
                display_name="x",
                unit="percent",
                frequency=FREQ_MONTHLY,
                seasonal_adjustment="unadjusted",
            )


# --------------------------------------------------------------------------- #
# Vintages
# --------------------------------------------------------------------------- #


class TestVintages:
    async def test_a_revision_is_a_new_row_and_the_old_one_survives(
        self, session
    ) -> None:
        series = await _series(session)
        await macro_store.record_observation(
            session, series, period_key="2025", value=400.0, vintage_at=JULY
        )
        await macro_store.record_observation(
            session, series, period_key="2025", value=412.0, vintage_at=OCTOBER
        )
        await session.commit()
        history = await macro_store.revision_history(
            session, series, period_key="2025"
        )
        assert [h.value for h in history] == [412.0, 400.0]
        assert [h.is_current for h in history] == [True, False]

    async def test_as_of_returns_what_the_platform_knew_then(self, session) -> None:
        """The property that makes a past report reproducible.

        A report written in August cited the July number and was right to. Reading the
        current value instead would silently re-base its macro context.
        """
        series = await _series(session)
        await macro_store.record_observation(
            session, series, period_key="2025", value=400.0, vintage_at=JULY
        )
        await macro_store.record_observation(
            session, series, period_key="2025", value=412.0, vintage_at=OCTOBER
        )
        await session.commit()
        august = await macro_store.as_of(session, series, AUGUST)
        assert [o.value for o in august] == [400.0]
        today = await macro_store.latest(session, series)
        assert [o.value for o in today] == [412.0]

    async def test_as_of_before_any_vintage_returns_nothing_not_the_first_value(
        self, session
    ) -> None:
        series = await _series(session)
        await macro_store.record_observation(
            session, series, period_key="2025", value=400.0, vintage_at=JULY
        )
        await session.commit()
        assert (
            await macro_store.as_of(
                session, series, datetime(2026, 1, 1, tzinfo=timezone.utc)
            )
            == []
        )

    async def test_backfilling_an_older_release_does_not_become_current(
        self, session
    ) -> None:
        """Loaded last is not published last."""
        series = await _series(session)
        await macro_store.record_observation(
            session, series, period_key="2025", value=412.0, vintage_at=OCTOBER
        )
        await macro_store.record_observation(
            session, series, period_key="2025", value=400.0, vintage_at=JULY
        )
        await session.commit()
        assert [o.value for o in await macro_store.latest(session, series)] == [412.0]

    async def test_writing_the_same_vintage_twice_is_a_conflict(self, session) -> None:
        """"The publisher re-released" and "our connector ran twice" are different
        events, and only the database can tell them apart reliably."""
        series = await _series(session)
        await macro_store.record_observation(
            session, series, period_key="2025", value=400.0, vintage_at=JULY
        )
        await session.commit()
        # The conflict surfaces at the flush inside `record_observation`, not at a
        # later commit — which is the right moment: the writer learns immediately
        # rather than after a transaction has accumulated other work.
        with pytest.raises(IntegrityError):
            await macro_store.record_observation(
                session, series, period_key="2025", value=999.0, vintage_at=JULY
            )
        await session.rollback()

    async def test_only_one_reading_per_period_is_current(self, session) -> None:
        series = await _series(session)
        for vintage, value in ((JULY, 400.0), (OCTOBER, 412.0)):
            await macro_store.record_observation(
                session, series, period_key="2025", value=value, vintage_at=vintage
            )
        await session.commit()
        from sqlalchemy import select

        current = (
            await session.execute(
                select(MacroObservation).where(
                    MacroObservation.series_id == series.id,
                    MacroObservation.is_current.is_(True),
                )
            )
        ).scalars().all()
        assert len(current) == 1

    async def test_a_published_absence_is_a_real_row(self, session) -> None:
        """"The Bank has no figure for 2025" is information. Dropping it makes the gap
        indistinguishable from the platform never having asked."""
        series = await _series(session)
        await macro_store.record_observation(
            session, series, period_key="2025", value=None, vintage_at=JULY
        )
        await session.commit()
        readings = await macro_store.latest(session, series)
        assert len(readings) == 1
        assert readings[0].value is None

    async def test_a_frequency_mismatch_is_refused(self, session) -> None:
        series = await _series(session)
        with pytest.raises(ValueError, match="looks continuous"):
            await macro_store.record_observation(
                session, series, period_key="2025-Q2", value=1.0, vintage_at=JULY
            )

    async def test_a_units_change_is_a_new_series_not_a_rewrite(self, session) -> None:
        dataset = await macro_store.upsert_dataset(session, _DATASET)
        await macro_store.upsert_series(session, dataset, _SERIES)
        with pytest.raises(ValueError, match="silently re-scale"):
            await macro_store.upsert_series(
                session,
                dataset,
                SeriesSpec(
                    series_key=_SERIES.series_key,
                    display_name=_SERIES.display_name,
                    unit="EUR",
                    frequency=FREQ_ANNUAL,
                ),
            )

    async def test_the_period_filter_is_in_the_same_statement_as_the_limit(
        self, session
    ) -> None:
        """A filter applied after a LIMIT returns the wrong rows. V3.3.2 shipped that
        once and it returned zero segment facts for a real company."""
        series = await _series(session)
        for year in range(2000, 2030):
            await macro_store.record_observation(
                session,
                series,
                period_key=str(year),
                value=float(year),
                vintage_at=JULY,
            )
        await session.commit()
        got = await macro_store.latest(
            session, series, period_keys=("2001", "2002"), limit=5
        )
        assert {o.period_key for o in got} == {"2001", "2002"}


# --------------------------------------------------------------------------- #
# The World Bank source
# --------------------------------------------------------------------------- #


def _wb_payload(
    *, rows: list | None = None, lastupdated: str | None = "2026-07-01"
) -> str:
    metadata: dict = {"page": 1, "pages": 1, "per_page": 50, "total": 2}
    if lastupdated is not None:
        metadata["lastupdated"] = lastupdated
    return json.dumps(
        [
            metadata,
            rows
            if rows is not None
            else [
                {
                    "indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP (current US$)"},
                    "country": {"id": "DK", "value": "Denmark"},
                    "date": "2024",
                    "value": 404000000000.0,
                },
                {
                    "indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP (current US$)"},
                    "country": {"id": "DK", "value": "Denmark"},
                    "date": "2025",
                    "value": None,
                },
            ],
        ]
    )


class TestWorldBankParsing:
    def test_a_good_response_yields_a_series_with_a_unit_from_the_name(self) -> None:
        fetch = parse_world_bank_payload(
            _wb_payload(), indicator="NY.GDP.MKTP.CD", geography="DNK"
        )
        assert fetch.status == FETCH_OK
        assert fetch.series.unit == "USD"
        assert fetch.series.frequency == FREQ_ANNUAL
        assert len(fetch.observations) == 2

    def test_a_published_null_is_kept_as_an_observation(self) -> None:
        fetch = parse_world_bank_payload(
            _wb_payload(), indicator="NY.GDP.MKTP.CD", geography="DNK"
        )
        assert [o.value for o in fetch.observations] == [404000000000.0, None]

    def test_an_indicator_whose_name_states_no_unit_is_unknown_not_dollars(
        self,
    ) -> None:
        rows = [
            {
                "indicator": {"id": "X", "value": "Some index"},
                "date": "2024",
                "value": 3.0,
            }
        ]
        fetch = parse_world_bank_payload(
            _wb_payload(rows=rows), indicator="X", geography="DNK"
        )
        assert fetch.series.unit == "unknown"

    def test_a_missing_vintage_is_refused_rather_than_stamped_now(self) -> None:
        """A vintage of "now" on a three-year-old release makes every historical reading
        look freshly published — exactly the lie the column exists to prevent."""
        fetch = parse_world_bank_payload(
            _wb_payload(lastupdated=None), indicator="X", geography="DNK"
        )
        assert fetch.status == FETCH_UNPARSEABLE
        assert "vintage" in (fetch.detail or "")

    def test_an_empty_row_set_is_no_data_not_an_error(self) -> None:
        fetch = parse_world_bank_payload(
            _wb_payload(rows=[]), indicator="X", geography="ZZZ"
        )
        assert fetch.status == FETCH_NO_DATA

    def test_a_non_json_body_is_unparseable(self) -> None:
        fetch = parse_world_bank_payload(
            "<html>rate limited</html>", indicator="X", geography="DNK"
        )
        assert fetch.status == FETCH_UNPARSEABLE

    def test_an_unreadable_value_is_skipped_not_recorded_as_an_absence(self) -> None:
        rows = [
            {"indicator": {"value": "GDP (current US$)"}, "date": "2024", "value": "n/a"}
        ]
        fetch = parse_world_bank_payload(
            _wb_payload(rows=rows), indicator="X", geography="DNK"
        )
        # A value that is present and unreadable is not a published NULL.
        assert fetch.status == FETCH_NO_DATA


class TestWorldBankFetching:
    async def test_it_makes_no_call_when_the_flag_is_off(self) -> None:
        calls: list = []

        async def _fetch(url, **kwargs):  # noqa: ANN001
            calls.append(url)

        source = WorldBankMacroSource(cfg=Settings(), fetcher=_fetch)
        fetch = await source.fetch_series(indicator="NY.GDP.MKTP.CD", geography="DNK")
        assert fetch.status == FETCH_NOT_CONFIGURED
        assert calls == []

    async def test_a_bad_code_is_refused_rather_than_escaped(self) -> None:
        """This becomes a URL path segment, and a segment built by escaping is one
        encoding bug from a different request."""
        calls: list = []

        async def _fetch(url, **kwargs):  # noqa: ANN001
            calls.append(url)

        source = WorldBankMacroSource(
            cfg=Settings(v3_macro_sources_enabled=True), fetcher=_fetch
        )
        fetch = await source.fetch_series(
            indicator="../../etc/passwd", geography="DNK"
        )
        assert fetch.status == FETCH_UNREACHABLE
        assert calls == []

    async def test_the_fetch_is_host_allowlisted_and_ip_pinned(self) -> None:
        seen: dict = {}

        class _Result:
            blocked = False
            ok = True
            error = None
            content = _wb_payload().encode()

        async def _fetch(url, *, allowed_domains, cfg=None, resolve_ip=False):  # noqa: ANN001
            seen["url"] = url
            seen["domains"] = allowed_domains
            seen["pinned"] = resolve_ip
            return _Result()

        source = WorldBankMacroSource(
            cfg=Settings(v3_macro_sources_enabled=True), fetcher=_fetch
        )
        fetch = await source.fetch_series(indicator="NY.GDP.MKTP.CD", geography="DNK")
        assert fetch.status == FETCH_OK
        assert seen["domains"] == ("api.worldbank.org",)
        assert seen["pinned"] is True
        assert seen["url"] == build_url("NY.GDP.MKTP.CD", "DNK", per_page=60)

    async def test_an_unreachable_publisher_is_not_an_empty_series(self) -> None:
        class _Blocked:
            blocked = True
            ok = False
            error = "host not in allowlist"
            content = None

        async def _fetch(url, **kwargs):  # noqa: ANN001
            return _Blocked()

        source = WorldBankMacroSource(
            cfg=Settings(v3_macro_sources_enabled=True), fetcher=_fetch
        )
        fetch = await source.fetch_series(indicator="NY.GDP.MKTP.CD", geography="DNK")
        assert fetch.status == FETCH_UNREACHABLE

    async def test_the_static_source_distinguishes_no_data_from_unreachable(
        self,
    ) -> None:
        source = StaticMacroSource(
            responses={("A", "DNK"): SeriesFetch(status=FETCH_UNREACHABLE)}
        )
        assert (await source.fetch_series(indicator="A", geography="DNK")).status == (
            FETCH_UNREACHABLE
        )
        assert (await source.fetch_series(indicator="B", geography="DNK")).status == (
            FETCH_NO_DATA
        )


# --------------------------------------------------------------------------- #
# The agent tool
# --------------------------------------------------------------------------- #


class _Context:
    def __init__(self, session) -> None:  # noqa: ANN001
        self.session = session


class TestMacroTool:
    def test_the_tool_is_registered_and_reads_nothing_untrusted(self) -> None:
        from app.services.agent_tools.builtin import register_builtins
        from app.services.agent_tools.registry import ToolRegistry

        registry = register_builtins(ToolRegistry())
        assert "get_macro_series" in registry.names()
        assert GET_MACRO_SERIES_SPEC.instrumented_units == ()

    def test_both_keys_are_required(self) -> None:
        with pytest.raises(ValueError, match="same series"):
            validate_get_macro_series({"series_key": "x"})

    def test_an_unreadable_as_of_is_refused_not_treated_as_now(self) -> None:
        """Silently becoming "now" returns today's revision of a series a past report
        cited, which is the one answer that looks right and is wrong."""
        with pytest.raises(ValueError, match="silently become"):
            validate_get_macro_series(
                {"dataset_key": "d", "series_key": "s", "as_of": "last summer"}
            )

    async def test_a_series_the_platform_does_not_hold_says_so(self, session) -> None:
        result = await GET_MACRO_SERIES_SPEC.handler(
            _Context(session),
            validate_get_macro_series({"dataset_key": "d", "series_key": "s"}),
        )
        assert result["found"] is False
        assert result["reason"] == "series_not_held"
        # No `series` key at all when there is nothing to describe.
        assert "series" not in result

    async def test_the_tool_returns_the_unit_and_frequency_with_every_value(
        self, session
    ) -> None:
        series = await _series(session)
        await macro_store.record_observation(
            session, series, period_key="2024", value=404.0, vintage_at=JULY
        )
        await session.commit()
        result = await GET_MACRO_SERIES_SPEC.handler(
            _Context(session),
            validate_get_macro_series(
                {"dataset_key": _DATASET.dataset_key, "series_key": _SERIES.series_key}
            ),
        )
        assert result["series"]["unit"] == "USD"
        assert result["series"]["frequency"] == FREQ_ANNUAL
        assert result["observations"][0]["unit"] == "USD"

    async def test_the_tool_honours_as_of(self, session) -> None:
        series = await _series(session)
        await macro_store.record_observation(
            session, series, period_key="2025", value=400.0, vintage_at=JULY
        )
        await macro_store.record_observation(
            session, series, period_key="2025", value=412.0, vintage_at=OCTOBER
        )
        await session.commit()
        result = await GET_MACRO_SERIES_SPEC.handler(
            _Context(session),
            validate_get_macro_series(
                {
                    "dataset_key": _DATASET.dataset_key,
                    "series_key": _SERIES.series_key,
                    "as_of": AUGUST.isoformat(),
                }
            ),
        )
        assert [o["value"] for o in result["observations"]] == [400.0]

    async def test_revisions_can_be_asked_for(self, session) -> None:
        series = await _series(session)
        await macro_store.record_observation(
            session, series, period_key="2025", value=400.0, vintage_at=JULY
        )
        await macro_store.record_observation(
            session, series, period_key="2025", value=412.0, vintage_at=OCTOBER
        )
        await session.commit()
        result = await GET_MACRO_SERIES_SPEC.handler(
            _Context(session),
            validate_get_macro_series(
                {
                    "dataset_key": _DATASET.dataset_key,
                    "series_key": _SERIES.series_key,
                    "include_revisions": True,
                }
            ),
        )
        assert [r["value"] for r in result["revisions"]["2025"]] == [412.0, 400.0]

    def test_the_tool_never_fetches(self) -> None:
        """A read-only tool that could trigger a network call would be an
        agent-controlled outbound request."""
        import ast
        from pathlib import Path

        source = Path("app/services/agent_tools/macro.py").read_text()
        names = {
            node.id
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Name)
        } | {
            node.attr
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Attribute)
        }
        assert "fetch_series" not in names
        assert "safe_fetch_document" not in names
        assert "WorldBankMacroSource" not in names
