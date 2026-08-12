"""Count the SQL statements a block of code issues.

Wall-clock timing on the small dev/test database is dominated by noise and
says almost nothing about an N+1: the whole point of an N+1 is that it scales
with row count, not that any single query is slow. Counting round trips is
both deterministic and the thing actually being fixed, so the regression tests
assert on counts.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession


class QueryCounter:
    """Records every statement executed while the counter is active."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    @property
    def count(self) -> int:
        return len(self.statements)

    @property
    def selects(self) -> list[str]:
        return [s for s in self.statements if s.lstrip().lower().startswith("select")]

    def against(self, table: str) -> list[str]:
        """Statements mentioning ``table``, for pinning which table is scanned."""
        return [s for s in self.statements if table.lower() in s.lower()]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"QueryCounter(count={self.count})"


@contextmanager
def count_queries(session: AsyncSession) -> Generator[QueryCounter, None, None]:
    """Count statements issued through ``session``'s engine.

    Listens on the *sync* engine underneath the AsyncSession, which is where
    SQLAlchemy's cursor events actually fire.
    """
    counter = QueryCounter()
    bind: Any = session.get_bind()
    target = getattr(bind, "sync_engine", bind)

    def _record(
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        counter.statements.append(statement)

    event.listen(target, "after_cursor_execute", _record)
    try:
        yield counter
    finally:
        event.remove(target, "after_cursor_execute", _record)
