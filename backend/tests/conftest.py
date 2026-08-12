"""Test fixtures for MassClaw backend.

ISOLATION
---------
This module rewrites DATABASE_URL / DATABASE_SYNC_URL / REDIS_URL **before**
any ``app.*`` module is imported, so the suite never touches the databases a
running dev server is using.

It has to happen here, at import time, because ``app.config.get_settings`` is
``@lru_cache``d: the first call freezes settings for the whole process, and
importing ``app.core.database`` below triggers that call. Environment
variables take precedence over ``.env`` in pydantic-settings, so setting them
here wins over ``backend/.env``.

Why this matters — the previous behaviour was:

* Redis: ``init_redis()`` built pools on db 0/1/2 of whatever ``REDIS_URL``
  pointed at, which locally is the same instance and same databases the dev
  server reads. Tests wrote real approval-shaped records into the live
  ``approval:pending`` set, and the dev server's approval janitor then tripped
  over them. Nothing flushed afterwards.
* Postgres: ``Base.metadata.create_all`` ran against the **dev** database, and
  the ``rollback()`` in ``db_session`` cannot undo work committed by code under
  test through its own ``db_session_context()`` — which the scheduler and the
  approval CRDT writers both do. Those rows persisted permanently.

Setup for a fresh machine: ``make test-db-setup`` (needs a superuser once, to
install the ``vector`` extension). CI does the equivalent in
``.github/workflows/ci.yml``.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from urllib.parse import urlsplit, urlunsplit

# --- Isolation, applied before any app import -----------------------------

#: Redis logical database reserved for the test suite. Kept clear of every
#: other consumer: the dev server uses 0/1/2 with Celery on 3, and
#: scripts/dev_federation_up.sh uses 10-12 for data and 13-15 for Celery.
#: 4-8 are left spare.
TEST_REDIS_DB = int(os.environ.get("MASSCLAW_TEST_REDIS_DB", "9"))

#: Suffix that marks a database as disposable.
_TEST_DB_SUFFIX = "_test"


def _redirect_to_test_database(url: str) -> str:
    """Point a Postgres URL at the throwaway sibling of its database.

    Idempotent: a URL already ending in ``_test`` is returned unchanged, so
    CI (which sets ``massclaw_test`` directly) is unaffected.
    """
    if not url:
        return url
    parts = urlsplit(url)
    name = parts.path.lstrip("/")
    if not name or name.endswith(_TEST_DB_SUFFIX):
        return url
    return urlunsplit(parts._replace(path=f"/{name}{_TEST_DB_SUFFIX}"))


def _redirect_to_test_redis_db(url: str) -> str:
    """Force a Redis URL onto the reserved test database.

    Note redis-py's ``ConnectionPool.from_url`` lets the db in the URL path
    win over an explicit ``db=`` kwarg, so this overrides the 0/1/2 split in
    ``RedisManager.initialize``: during tests cache, pub/sub and rate-limit
    keys all share one database. That is already how CI behaved (it sets
    ``/0``), and the three use distinct key prefixes, so nothing collides.
    """
    if not url:
        return url
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{TEST_REDIS_DB}"))


for _var, _rewrite in (
    ("DATABASE_URL", _redirect_to_test_database),
    ("DATABASE_SYNC_URL", _redirect_to_test_database),
    ("REDIS_URL", _redirect_to_test_redis_db),
):
    # Fall back to the app default when the var is unset, so an unconfigured
    # machine still gets redirected rather than silently using the dev default.
    _current = os.environ.get(_var)
    if _current is None:
        from app.config import Settings as _Settings  # noqa: PLC0415

        _current = getattr(_Settings(), _var.lower())
    os.environ[_var] = _rewrite(_current)

# --- Now safe to import the app -------------------------------------------

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.database import Base, dispose_db, get_engine, get_session_factory, init_db  # noqa: E402
from app.core.redis import dispose_redis, get_redis_manager, init_redis  # noqa: E402
from app.models import *  # noqa: F401, F403, E402 — ensure all models registered


def pytest_report_header() -> list[str]:
    """Make the isolation visible in the test header.

    If a run ever starts hitting the dev database again, this is where you
    find out — before it has written anything.
    """
    settings = get_settings()
    return [
        f"massclaw db:    {settings.database_url.rsplit('/', 1)[-1]}",
        f"massclaw redis: {settings.redis_url}",
    ]


@pytest.fixture(scope="session", autouse=True)
def _guard_test_targets() -> None:
    """Refuse to run against a non-test database.

    A misconfigured ``DATABASE_URL`` would otherwise let ``create_all`` and a
    few hundred committed rows land in the dev database, which is exactly the
    failure this module exists to prevent. Better to stop before the first
    test than to clean up afterwards.
    """
    settings = get_settings()
    db_name = urlsplit(settings.database_url).path.lstrip("/")
    if not db_name.endswith(_TEST_DB_SUFFIX):
        pytest.exit(
            f"Refusing to run: DATABASE_URL points at {db_name!r}, which is not "
            f"a *{_TEST_DB_SUFFIX} database. Tests commit real rows and would "
            f"pollute it.",
            returncode=1,
        )
    if urlsplit(settings.redis_url).path.lstrip("/") != str(TEST_REDIS_DB):
        pytest.exit(
            f"Refusing to run: REDIS_URL is not on the reserved test database "
            f"{TEST_REDIS_DB} (got {settings.redis_url!r}).",
            returncode=1,
        )


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _sync_pg_enum_values() -> AsyncGenerator[None, None]:
    """Add enum values the models declare but an older test database lacks.

    ``create_all`` creates a Postgres enum type with every value it knows, but
    it will not *alter* one that already exists — and the test database is
    persistent on a dev machine. So adding a member to a Python enum passes on
    a fresh CI database (built from scratch each run) and fails locally with
    ``invalid input value for enum ...``, or the reverse once a migration
    lands. Reconciling here keeps the two honest, and mirrors what the alembic
    ``ALTER TYPE ... ADD VALUE`` migrations do to real databases.

    Discovered from the metadata rather than a hand-maintained list, so a new
    enum member needs no change to this fixture.
    """
    from sqlalchemy import Enum as SAEnum
    from sqlalchemy import text

    await dispose_db()
    init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    declared: dict[str, list[str]] = {}
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, SAEnum) and column.type.name:
                declared.setdefault(column.type.name, []).extend(column.type.enums)

    # AUTOCOMMIT because ALTER TYPE ... ADD VALUE cannot run in a transaction
    # block on Postgres before 12, and the value is unusable until commit even
    # after that.
    async with engine.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
        for type_name, values in declared.items():
            for value in dict.fromkeys(values):
                await conn.execute(text(f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{value}'"))

    await dispose_db()
    yield


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _flush_test_redis() -> AsyncGenerator[None, None]:
    """Start and finish with an empty test Redis database.

    Flushing up front clears anything a crashed earlier run left behind;
    flushing afterwards keeps `redis-cli` on db 9 readable when debugging.
    Safe because db 9 is reserved for the suite — see TEST_REDIS_DB.
    """

    async def _flush() -> None:
        try:
            await dispose_redis()
        except Exception:
            pass
        await init_redis()
        try:
            await get_redis_manager().get_cache_client().flushdb()
        finally:
            await dispose_redis()

    await _flush()
    yield
    await _flush()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Per-test database session with rollback.

    Disposes and re-creates the engine each time to ensure the connection pool
    is bound to the current event loop (prevents 'Future attached to a different
    loop' errors with function-scoped asyncio event loops).
    """
    await dispose_db()
    init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = get_session_factory()
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def redis_client():
    """Per-test Redis client."""
    try:
        await dispose_redis()
    except Exception:
        pass
    await init_redis()
    client = get_redis_manager().get_cache_client()
    yield client


@pytest_asyncio.fixture
async def sample_agent(db_session):
    """Create a sample agent for testing."""
    from app.models.agent import Agent
    from app.models.base import AgentStatus

    agent = Agent(
        name=f"test-agent-{uuid.uuid4().hex[:8]}",
        description="Test agent for unit tests",
        capabilities=["test", "research", "analysis"],
        endpoint="internal://test",
        trust_score=0.75,
        status=AgentStatus.ACTIVE,
        cost_profile={"avg_cost_per_call": 0.01},
        latency_profile={"p50_ms": 500, "p95_ms": 2000},
    )
    db_session.add(agent)
    await db_session.flush()
    await db_session.refresh(agent)
    return agent


@pytest_asyncio.fixture
async def sample_workflow(db_session):
    """Create a sample workflow for testing."""
    from app.models.workflow import Workflow

    wf = Workflow(
        user_id="test-user",
        prompt="Test workflow prompt for analysis",
        domain="test",
        budget_limit=500,
    )
    db_session.add(wf)
    await db_session.flush()
    await db_session.refresh(wf)
    return wf
