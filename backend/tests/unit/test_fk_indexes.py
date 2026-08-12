"""Every foreign key should be indexed on the referencing side.

Postgres indexes only the *referenced* side of a foreign key automatically
(via the primary key or unique constraint it points at). The referencing column
gets nothing unless you ask, so an unindexed FK means both a sequential scan
for any lookup that follows it and a child-table scan when the parent row is
deleted under ``ON DELETE SET NULL``.

This started as seven specific columns — every ``agent_id`` FK was indexed
while the self-referential and secondary ones were not — but the check is
written generically so a *new* unindexed FK fails too, rather than only the
seven that were fixed.
"""

from __future__ import annotations

from sqlalchemy import Table

from app.models.base import Base

# Columns deliberately left unindexed, with the reason. Empty today; an entry
# here is a considered decision (e.g. a tiny lookup table where the index would
# cost more than it saves), not a way to silence the test.
ALLOWED_UNINDEXED: dict[str, str] = {}


def _indexed_columns(table: Table) -> set[str]:
    """Columns covered as the *leading* column of some index or constraint.

    Only the leading column counts: a composite index on ``(a, b)`` does not
    help a lookup on ``b`` alone.
    """
    covered: set[str] = set()

    for index in table.indexes:
        cols = list(index.columns)
        if cols:
            covered.add(cols[0].name)

    # A primary key or unique constraint is backed by an index too.
    if table.primary_key is not None:
        pk_cols = list(table.primary_key.columns)
        if pk_cols:
            covered.add(pk_cols[0].name)

    for constraint in table.constraints:
        cols = list(getattr(constraint, "columns", []))
        if cols and type(constraint).__name__ == "UniqueConstraint":
            covered.add(cols[0].name)

    return covered


def _foreign_key_columns(table: Table) -> set[str]:
    return {column.name for column in table.columns if column.foreign_keys}


def test_every_foreign_key_column_is_indexed() -> None:
    unindexed: list[str] = []

    for table in Base.metadata.sorted_tables:
        covered = _indexed_columns(table)
        for column_name in sorted(_foreign_key_columns(table)):
            qualified = f"{table.name}.{column_name}"
            if column_name in covered or qualified in ALLOWED_UNINDEXED:
                continue
            unindexed.append(qualified)

    assert not unindexed, (
        "Unindexed foreign key column(s): "
        + ", ".join(unindexed)
        + ". Add index=True to the mapped_column and generate a migration, or "
        "record a justification in ALLOWED_UNINDEXED."
    )


def test_the_seven_columns_this_check_was_written_for_are_indexed() -> None:
    """Explicit list, so the generic check above cannot pass by accident (for
    example if `_indexed_columns` were ever loosened)."""
    expected = {
        "memory_records": "parent_version_id",
        "trust_events": "workflow_id",
        "agent_scores": "workflow_id",
        "tasks": "parent_task_id",
        "sprint_tasks": "workflow_id",
    }
    tables = {table.name: table for table in Base.metadata.sorted_tables}

    for table_name, column_name in expected.items():
        assert column_name in _indexed_columns(tables[table_name]), f"{table_name}.{column_name} lost its index"

    # Two more on tasks and trust_events that share a table with the above.
    assert "blocked_by_task_id" in _indexed_columns(tables["tasks"])
    assert "task_id" in _indexed_columns(tables["trust_events"])
