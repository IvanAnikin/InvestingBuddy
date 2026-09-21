"""Migration 041 — additive, reversible, and safe to apply before the code that uses it.

The deploy order is the point. 041 ships with NO ORM change so that applying it is inert;
the slices that map the new columns merge only after it has been applied. The reverse
order — ORM columns on a schema that lacks them — fails every query on the table.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
import sqlalchemy as sa

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / (
    "041_add_research_question_graph.py"
)
POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")


def _module():
    spec = importlib.util.spec_from_file_location("_m041", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestItIsAdditive:
    def test_it_follows_040(self) -> None:
        m = _module()
        assert (m.revision, m.down_revision) == ("041", "040")

    def test_every_column_is_nullable_with_no_default(self) -> None:
        """A NOT NULL column or a server default rewrites existing rows; NULL must keep
        meaning 'written before V3.18'."""
        m = _module()
        for table, name, type_ in m._COLUMNS:
            column = m._column(name, type_)
            assert column.nullable is True, f"{table}.{name}"
            assert column.server_default is None, f"{table}.{name}"
            assert column.default is None, f"{table}.{name}"

    def test_it_only_touches_tables_that_already_exist(self) -> None:
        tables = {table for table, _name, _type in _module()._COLUMNS}
        assert tables == {
            "research_questions",
            "research_findings",
            "research_gaps",
            "research_runs",
            "research_leads",
            "macro_series",
        }

    def test_the_source_alters_and_drops_nothing_in_upgrade(self) -> None:
        source = MIGRATION.read_text()
        upgrade = source[source.index("def upgrade()") : source.index("def downgrade()")]
        for forbidden in ("drop_", "alter_column", "execute(", "create_check", "rename"):
            assert forbidden not in upgrade, forbidden

    def test_no_index_is_unique(self) -> None:
        """A unique index can fail on existing rows; these are lookups only."""
        source = MIGRATION.read_text()
        assert "unique=True" not in source


@pytest.mark.skipif(not POSTGRES_URL, reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head")
class TestOnRealPostgres:
    def test_the_columns_exist_at_head(self) -> None:
        url = POSTGRES_URL.replace("+asyncpg", "+psycopg")
        engine = sa.create_engine(url)
        try:
            inspector = sa.inspect(engine)
            for table, name, _type in _module()._COLUMNS:
                names = {c["name"] for c in inspector.get_columns(table)}
                assert name in names, f"{table}.{name} missing at head"
        finally:
            engine.dispose()
