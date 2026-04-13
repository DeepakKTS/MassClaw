from __future__ import annotations

import time
from collections.abc import Callable, Coroutine
from enum import Enum
from typing import Any, TypeVar

import redis.asyncio as aioredis

from app.exceptions import CircuitBreakerOpenError

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Redis-backed circuit breaker for cross-process visibility.

    States:
        CLOSED: Normal operation. Failures are counted.
        OPEN: Failing. All calls rejected immediately.
        HALF_OPEN: Testing recovery. Limited calls allowed.
    """

    def __init__(
        self,
        name: str,
        redis: aioredis.Redis,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 3,
    ) -> None:
        self.name = name
        self.redis = redis
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self._key_prefix = f"circuit_breaker:{name}"

    async def _get_state(self) -> CircuitState:
        state = await self.redis.get(f"{self._key_prefix}:state")
        if state is None:
            return CircuitState.CLOSED
        return CircuitState(state)

    async def _set_state(self, state: CircuitState) -> None:
        await self.redis.set(f"{self._key_prefix}:state", state.value)

    async def _get_failure_count(self) -> int:
        count = await self.redis.get(f"{self._key_prefix}:failures")
        return int(count) if count else 0

    async def _increment_failures(self) -> int:
        key = f"{self._key_prefix}:failures"
        count = await self.redis.incr(key)
        await self.redis.expire(key, int(self.recovery_timeout * 2))
        return int(count)

    async def _reset_failures(self) -> None:
        await self.redis.delete(f"{self._key_prefix}:failures")

    async def _get_opened_at(self) -> float:
        val = await self.redis.get(f"{self._key_prefix}:opened_at")
        return float(val) if val else 0.0

    async def _set_opened_at(self) -> None:
        await self.redis.set(f"{self._key_prefix}:opened_at", str(time.time()))

    async def _get_half_open_calls(self) -> int:
        val = await self.redis.get(f"{self._key_prefix}:half_open_calls")
        return int(val) if val else 0

    async def _increment_half_open_calls(self) -> int:
        key = f"{self._key_prefix}:half_open_calls"
        count = await self.redis.incr(key)
        await self.redis.expire(key, int(self.recovery_timeout * 2))
        return int(count)

    async def _reset_half_open_calls(self) -> None:
        await self.redis.delete(f"{self._key_prefix}:half_open_calls")

    async def call(
        self,
        func: Callable[..., Coroutine[Any, Any, T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute a function through the circuit breaker."""
        state = await self._get_state()

        if state == CircuitState.OPEN:
            opened_at = await self._get_opened_at()
            if time.time() - opened_at >= self.recovery_timeout:
                await self._set_state(CircuitState.HALF_OPEN)
                await self._reset_half_open_calls()
                state = CircuitState.HALF_OPEN
            else:
                raise CircuitBreakerOpenError(service=self.name)

        if state == CircuitState.HALF_OPEN:
            current_calls = await self._get_half_open_calls()
            if current_calls >= self.half_open_max_calls:
                raise CircuitBreakerOpenError(service=self.name)
            await self._increment_half_open_calls()

        try:
            result = await func(*args, **kwargs)
            await self._record_success(state)
            return result
        except Exception:
            await self._record_failure(state)
            raise

    async def _record_success(self, current_state: CircuitState) -> None:
        if current_state == CircuitState.HALF_OPEN:
            await self._set_state(CircuitState.CLOSED)
            await self._reset_failures()
            await self._reset_half_open_calls()
        elif current_state == CircuitState.CLOSED:
            await self._reset_failures()

    async def _record_failure(self, current_state: CircuitState) -> None:
        if current_state == CircuitState.HALF_OPEN:
            await self._set_state(CircuitState.OPEN)
            await self._set_opened_at()
        elif current_state == CircuitState.CLOSED:
            failures = await self._increment_failures()
            if failures >= self.failure_threshold:
                await self._set_state(CircuitState.OPEN)
                await self._set_opened_at()

    async def get_status(self) -> dict[str, Any]:
        """Get current circuit breaker status for monitoring."""
        state = await self._get_state()
        return {
            "name": self.name,
            "state": state.value,
            "failure_count": await self._get_failure_count(),
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
        }
