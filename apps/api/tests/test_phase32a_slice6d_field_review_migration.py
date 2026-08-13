"""
Phase 32A Slice 6D — migration 015 structural smoke test.

No migration test existed in this repo before, so this is a lightweight,
dependency-free check that runs in CI without a database:

  * ``015`` chains onto ``014`` and is the single head;
  * ``upgrade()`` creates exactly the two Deep Field Review tables, with the FK
    behaviour the models declare (CASCADE from the discovery run, SET NULL on the
    candidate/report links so research history survives a deletion);
  * ``downgrade()`` is SYMMETRICAL — every table and index created is dropped,
    and nothing else is touched;
  * the migration's columns match the SQLAlchemy models exactly, so the ORM and
    the schema cannot drift.

``upgrade()``/``downgrade()`` are executed against a recording fake ``op``, so no
Postgres is needed. A real ``alembic upgrade head`` / ``downgrade -1`` against
local Postgres is part of the PR's manual verification.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from app.models.field_review import FieldReviewCandidateSummary, FieldReviewRun

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "015_add_field_review.py"

EXPECTED_TABLES = ("field_review_runs", "field_review_candidate_summaries")


# ---------------------------------------------------------------------------
# Recording fake ``op``
# ---------------------------------------------------------------------------


class _RecordingOp:
    """Captures the DDL calls a migration makes, without a database."""

    def __init__(self) -> None:
        self.created_tables: list[tuple[str, tuple[Any, ...]]] = []
        self.dropped_tables: list[str] = []
        self.created_indexes: list[tuple[str, str, list[str]]] = []
        self.dropped_indexes: list[tuple[str, str]] = []

    def create_table(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.created_tables.append((name, args))

    def drop_table(self, name: str, **kwargs: Any) -> None:
        self.dropped_tables.append(name)

    def create_index(
        self, index_name: str, table_name: str, columns: list[str], **kwargs: Any
    ) -> None:
        self.created_indexes.append((index_name, table_name, list(columns)))

    def drop_index(self, index_name: str, table_name: str = "", **kwargs: Any) -> None:
        self.dropped_indexes.append((index_name, table_name or kwargs.get("table_name", "")))


@pytest.fixture
def migration():
    spec = importlib.util.spec_from_file_location(
        "migration_015_add_field_review", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def applied(migration, monkeypatch):
    """Run upgrade() then downgrade() against a recording fake ``op``."""
    recorder = _RecordingOp()
    monkeypatch.setattr(migration, "op", recorder)
    migration.upgrade()
    upgrade_state = _RecordingOp()
    upgrade_state.created_tables = list(recorder.created_tables)
    upgrade_state.created_indexes = list(recorder.created_indexes)
    migration.downgrade()
    recorder.created_tables = upgrade_state.created_tables
    recorder.created_indexes = upgrade_state.created_indexes
    return recorder


# ---------------------------------------------------------------------------
# Revision chaining
# ---------------------------------------------------------------------------


def test_015_chains_onto_014(migration) -> None:
    assert migration.revision == "015"
    assert migration.down_revision == "014"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_015_has_at_most_one_child() -> None:
    """015 stays a single, non-branching link in the chain (one linear head).

    Superseded by migration 016 (Phase 32A corrective, Problem B) — 015 is no
    longer the overall head, but it must still have EXACTLY one child (016),
    never two (which would mean a branch)."""
    down_revisions: list[str] = []
    revisions: list[str] = []
    for path in VERSIONS_DIR.glob("*.py"):
        text = path.read_text()
        for line in text.splitlines():
            if line.startswith("revision: str = "):
                revisions.append(line.split("=", 1)[1].strip().strip('"'))
            if line.startswith("down_revision: str | None = "):
                down_revisions.append(line.split("=", 1)[1].strip().strip('"'))
    assert "015" in revisions
    assert revisions.count("015") == 1
    # Exactly one migration chains onto 015 — never a branch.
    assert down_revisions.count("015") == 1


# ---------------------------------------------------------------------------
# Upgrade shape
# ---------------------------------------------------------------------------


def test_upgrade_creates_exactly_the_two_field_review_tables(applied) -> None:
    assert [name for name, _ in applied.created_tables] == list(EXPECTED_TABLES)


def test_upgrade_creates_the_declared_indexes(applied) -> None:
    created = {name for name, _, _ in applied.created_indexes}
    assert created == {
        "ix_field_review_runs_discovery_run_id",
        "ix_field_review_runs_status",
        "ix_field_review_runs_created_at",
        "ix_field_review_candidate_summaries_run_id",
        "ix_field_review_candidate_summaries_candidate_id",
        "ix_field_review_candidate_summaries_report_id",
    }
    # Every index belongs to one of THIS migration's tables.
    assert {table for _, table, _ in applied.created_indexes} <= set(EXPECTED_TABLES)


def _constraints(applied, table: str) -> list[Any]:
    args = next(a for name, a in applied.created_tables if name == table)
    return [a for a in args if not hasattr(a, "type")]


def _column_names(applied, table: str) -> set[str]:
    args = next(a for name, a in applied.created_tables if name == table)
    return {a.name for a in args if hasattr(a, "type")}


def test_field_review_runs_cascades_from_the_discovery_run(applied) -> None:
    fks = [
        c
        for c in _constraints(applied, "field_review_runs")
        if type(c).__name__ == "ForeignKeyConstraint"
    ]
    assert len(fks) == 1
    assert fks[0].ondelete == "CASCADE"


def test_candidate_summary_links_use_set_null_to_preserve_history(applied) -> None:
    """Deleting a candidate/report must never erase the record of what was
    compared; only deleting the field review itself cascades."""
    fks = {
        c.name: c
        for c in _constraints(applied, "field_review_candidate_summaries")
        if type(c).__name__ == "ForeignKeyConstraint"
    }
    assert (
        fks["fk_field_review_candidate_summaries_run_id_field_review_runs"].ondelete
        == "CASCADE"
    )
    assert (
        fks["fk_field_review_summaries_candidate_id_discovery_candidates"].ondelete
        == "SET NULL"
    )
    assert (
        fks["fk_field_review_candidate_summaries_report_id_reports"].ondelete
        == "SET NULL"
    )


def test_citation_ref_is_unique_per_field_review_run(applied) -> None:
    uniques = [
        c
        for c in _constraints(applied, "field_review_candidate_summaries")
        if type(c).__name__ == "UniqueConstraint"
    ]
    assert len(uniques) == 1
    assert uniques[0].name == "uq_field_review_candidate_summary_run_ref"


@pytest.mark.parametrize(
    ("table", "model"),
    [
        ("field_review_runs", FieldReviewRun),
        ("field_review_candidate_summaries", FieldReviewCandidateSummary),
    ],
)
def test_migration_columns_match_the_orm_model_exactly(applied, table, model) -> None:
    """The schema and the ORM must not drift."""
    assert _column_names(applied, table) == {c.name for c in model.__table__.columns}


# PostgreSQL truncates/rejects identifiers over this length. A too-long
# constraint name only blows up when the migration actually runs against
# Postgres, so it is asserted here where CI (which has no Postgres) can catch it.
_PG_MAX_IDENTIFIER_LEN = 63


def test_every_identifier_the_migration_creates_fits_postgres(applied) -> None:
    identifiers: list[str] = [name for name, _ in applied.created_tables]
    identifiers += [name for name, _, _ in applied.created_indexes]
    for table, _ in applied.created_tables:
        identifiers += [
            c.name for c in _constraints(applied, table) if getattr(c, "name", None)
        ]
    too_long = [i for i in identifiers if len(i) > _PG_MAX_IDENTIFIER_LEN]
    assert too_long == [], too_long


@pytest.mark.parametrize(
    "model", [FieldReviewRun, FieldReviewCandidateSummary]
)
def test_every_orm_identifier_fits_postgres(model) -> None:
    """The ORM's own names must be Postgres-legal too (they must MATCH the
    migration's, so a too-long ORM name is the same defect)."""
    table = model.__table__
    identifiers = [table.name]
    identifiers += [c.name for c in table.constraints if c.name is not None]
    identifiers += [i.name for i in table.indexes if i.name is not None]
    for column in table.columns:
        identifiers += [
            fk.constraint.name
            for fk in column.foreign_keys
            if fk.constraint is not None and fk.constraint.name is not None
        ]
    too_long = [str(i) for i in identifiers if len(str(i)) > _PG_MAX_IDENTIFIER_LEN]
    assert too_long == [], too_long


# ---------------------------------------------------------------------------
# Downgrade symmetry
# ---------------------------------------------------------------------------


def test_downgrade_drops_every_table_it_created_and_nothing_more(applied) -> None:
    assert sorted(applied.dropped_tables) == sorted(EXPECTED_TABLES)


def test_downgrade_drops_every_index_it_created_and_nothing_more(applied) -> None:
    created = {name for name, _, _ in applied.created_indexes}
    dropped = {name for name, _ in applied.dropped_indexes}
    assert dropped == created


def test_downgrade_drops_the_child_table_before_the_parent(applied) -> None:
    """FK ordering: the CASCADE child must go first or the drop would fail."""
    assert applied.dropped_tables.index(
        "field_review_candidate_summaries"
    ) < applied.dropped_tables.index("field_review_runs")
