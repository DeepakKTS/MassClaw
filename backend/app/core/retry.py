from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


def log_retry(retry_state: RetryCallState) -> None:
    """Log retry attempts for observability."""
    if retry_state.attempt_number > 1:
        exception = retry_state.outcome.exception() if retry_state.outcome else None
        logger.warning(
            "retrying_operation",
            attempt=retry_state.attempt_number,
            error=str(exception) if exception else None,
            wait_time=retry_state.next_action.sleep if retry_state.next_action else None,  # type: ignore[union-attr]
        )


def with_retry(
    max_attempts: int = 3,
    min_wait: float = 1.0,
    max_wait: float = 30.0,
    retry_on: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[Callable[..., Coroutine[Any, Any, T]]], Callable[..., Coroutine[Any, Any, T]]]:
    """Decorator for async functions that should be retried on failure.

    Uses exponential backoff: wait = min(max_wait, min_wait * 2^(attempt-1))
    """
    return retry(  # type: ignore[return-value]
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=min_wait, max=max_wait),
        retry=retry_if_exception_type(retry_on),
        before_sleep=log_retry,
        reraise=True,
    )
