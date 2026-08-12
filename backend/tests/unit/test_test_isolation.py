"""Guards the test suite's own isolation from the dev environment.

Isolation is enforced by URL rewriting at ``tests/conftest.py`` import time,
which is easy to break silently — a stray early import of ``app.config``, a
changed default, or someone exporting ``DATABASE_URL`` in their shell would
all quietly send the suite back at the dev database. Tests commit real rows
and write real Redis keys, so "quietly" is the dangerous part.

These assertions cost nothing and fail loudly the moment that happens.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from app.config import get_settings
from app.core.redis import get_redis_manager
from tests.conftest import (
    TEST_REDIS_DB,
    _redirect_to_test_database,
    _redirect_to_test_redis_db,
)


class TestEffectiveSettings:
    """What the app will actually connect to during this run."""

    def test_database_is_a_test_database(self) -> None:
        name = urlsplit(get_settings().database_url).path.lstrip("/")
        assert name.endswith("_test"), f"suite is pointed at {name!r}"

    def test_sync_database_is_a_test_database(self) -> None:
        name = urlsplit(get_settings().database_sync_url).path.lstrip("/")
        assert name.endswith("_test"), f"suite is pointed at {name!r}"

    def test_redis_is_on_the_reserved_db(self) -> None:
        path = urlsplit(get_settings().redis_url).path.lstrip("/")
        assert path == str(TEST_REDIS_DB)

    def test_reserved_redis_db_avoids_every_other_consumer(self) -> None:
        """0-2 are the dev server's, 3 is Celery, 10-15 are the federation
        nodes (scripts/dev_federation_up.sh)."""
        assert TEST_REDIS_DB not in {0, 1, 2, 3}
        assert TEST_REDIS_DB not in range(10, 16)


class TestLiveRedisConnection:
    """The rewrite is only worth anything if the pools honour it."""

    async def test_clients_are_bound_to_the_test_db(self, redis_client) -> None:
        assert redis_client.connection_pool.connection_kwargs["db"] == TEST_REDIS_DB

    async def test_all_three_pools_are_bound_to_the_test_db(self, redis_client) -> None:
        """redis-py lets the db in the URL path win over the ``db=`` kwarg
        that ``RedisManager.initialize`` passes, so cache/pubsub/rate-limit
        all collapse onto the test db. Assert that rather than leave it as
        folklore — if a future redis-py flips the precedence, two of these
        pools would silently point at the dev databases."""
        mgr = get_redis_manager()
        for client in (
            mgr.get_cache_client(),
            mgr.get_pubsub_client(),
            mgr.get_rate_limit_client(),
        ):
            assert client.connection_pool.connection_kwargs["db"] == TEST_REDIS_DB


class TestRewriteHelpers:
    def test_database_rewrite_appends_suffix(self) -> None:
        assert (
            _redirect_to_test_database("postgresql+asyncpg://u:p@h:5432/massclaw")
            == "postgresql+asyncpg://u:p@h:5432/massclaw_test"
        )

    def test_database_rewrite_is_idempotent(self) -> None:
        """CI sets ``massclaw_test`` directly; rewriting must not produce
        ``massclaw_test_test``."""
        url = "postgresql+asyncpg://u:p@h:5432/massclaw_test"
        assert _redirect_to_test_database(url) == url
        assert _redirect_to_test_database(_redirect_to_test_database(url)) == url

    def test_database_rewrite_preserves_credentials_and_port(self) -> None:
        out = _redirect_to_test_database("postgresql://user:pw@example.com:6543/db")
        assert out == "postgresql://user:pw@example.com:6543/db_test"

    def test_redis_rewrite_replaces_existing_db(self) -> None:
        assert _redirect_to_test_redis_db("redis://localhost:6379/0") == f"redis://localhost:6379/{TEST_REDIS_DB}"

    def test_redis_rewrite_adds_missing_db(self) -> None:
        assert _redirect_to_test_redis_db("redis://localhost:6379") == f"redis://localhost:6379/{TEST_REDIS_DB}"

    def test_redis_rewrite_is_idempotent(self) -> None:
        url = f"redis://localhost:6379/{TEST_REDIS_DB}"
        assert _redirect_to_test_redis_db(url) == url

    def test_rewrites_tolerate_empty_input(self) -> None:
        assert _redirect_to_test_database("") == ""
        assert _redirect_to_test_redis_db("") == ""
