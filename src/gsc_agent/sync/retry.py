"""Retry Search Console calls that fail because of rate limits or transport errors."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

from gsc_agent.errors import RETRYABLE_TYPES

T = TypeVar("T")


def call_with_retry(
    func: Callable[[], T],
    *,
    max_attempts: int = 5,
    sleep: Callable[[float], None] = time.sleep,
    uniform: Callable[[float, float], float] = random.uniform,
) -> tuple[T, int]:
    """Return ``(result, retry_count)``. Non-retryable errors propagate immediately."""
    delay = 1.0
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return func(), attempt
        except RETRYABLE_TYPES as exc:
            last_error = exc
            if attempt == max_attempts - 1:
                break
            sleep(delay + uniform(0, 0.25))
            delay *= 2
    assert last_error is not None
    raise last_error
