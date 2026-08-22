"""Bounded retries for one idempotent outbound model completion request.

This module deliberately sits below the agent loop and above LiteLLM transport.  It
never retries MCP tools, transcript mutation, or provider failover.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import logging
import random
import time
from typing import Any, Awaitable, Callable, Literal

from litellm.exceptions import (
    APIConnectionError,
    APIError,
    BadGatewayError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)


logger = logging.getLogger("api_service.agent.completion_retry")

RetryCategory = Literal["throttled", "transient"]


@dataclass(frozen=True)
class CompletionRetryPolicy:
    """Internal bounded retry controls for one logical model completion."""

    max_attempts: int
    max_elapsed_seconds: float
    transient_base_seconds: float
    throttled_base_seconds: float
    max_backoff_seconds: float

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("LLM_MAX_ATTEMPTS must be at least 1")
        if self.max_elapsed_seconds <= 0:
            raise ValueError("LLM_RETRY_MAX_ELAPSED_SECONDS must be positive")
        if self.transient_base_seconds < 0:
            raise ValueError("LLM_RETRY_TRANSIENT_BASE_SECONDS must not be negative")
        if self.throttled_base_seconds < 0:
            raise ValueError("LLM_RETRY_THROTTLED_BASE_SECONDS must not be negative")
        if self.max_backoff_seconds < 0:
            raise ValueError("LLM_RETRY_MAX_BACKOFF_SECONDS must not be negative")


def retry_category(exc: Exception) -> RetryCategory | None:
    """Return an approved retry category, never retrying unknown failures."""
    if isinstance(exc, RateLimitError):
        return "throttled"
    if isinstance(
        exc,
        (
            APIConnectionError,
            Timeout,
            BadGatewayError,
            InternalServerError,
            ServiceUnavailableError,
        ),
    ):
        return "transient"
    if isinstance(exc, APIError) and getattr(exc, "status_code", None) in {
        408,
        429,
        500,
        502,
        503,
        504,
    }:
        return "throttled" if exc.status_code == 429 else "transient"
    return None


def retry_after_seconds(exc: Exception, now: datetime | None = None) -> float | None:
    """Parse a provider Retry-After header without trusting invalid values."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    raw = (
        headers.get("Retry-After") or headers.get("retry-after")
        if headers is not None
        else None
    )
    if not raw:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(raw)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        reference = now or datetime.now(UTC)
        seconds = (retry_at - reference).total_seconds()
    return max(0.0, seconds)


class CompletionRetryExecutor:
    """Retry a single already-serialized LiteLLM request within one deadline."""

    def __init__(
        self,
        policy: CompletionRetryPolicy,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._policy = policy
        self._monotonic = monotonic
        self._sleep = sleep
        self._uniform = uniform

    async def run(
        self,
        call: Callable[[float], Awaitable[Any]],
        *,
        provider_timeout: float,
        model: str,
        provider: str | None,
        on_attempt: Callable[[], None],
        on_retry: Callable[[RetryCategory, float], None],
        on_exhausted: Callable[[RetryCategory, str], None],
        on_suppressed: Callable[[str], None],
    ) -> Any:
        """Return a response or re-raise the final upstream exception.

        ``call`` receives the remaining per-attempt timeout.  The caller owns
        request serialization, so every physical attempt has identical payload
        fields except the deadline-clamped timeout.
        """
        deadline = self._monotonic() + self._policy.max_elapsed_seconds
        last_category: RetryCategory | None = None
        last_retryable_error: Exception | None = None

        for attempt in range(1, self._policy.max_attempts + 1):
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                category = last_category or "transient"
                on_exhausted(category, "deadline")
                logger.warning(
                    "[LLM] retry deadline reached model=%s provider=%s attempt=%d "
                    "last_exception=%s last_status=%s",
                    model,
                    provider or "(inferred)",
                    attempt,
                    type(last_retryable_error).__name__
                    if last_retryable_error is not None
                    else None,
                    getattr(last_retryable_error, "status_code", None),
                )
                if last_retryable_error is not None:
                    raise last_retryable_error
                raise TimeoutError("LLM retry deadline reached before first attempt")

            on_attempt()
            try:
                return await call(min(provider_timeout, remaining))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                category = retry_category(exc)
                if category is None:
                    on_suppressed("non_retryable_error")
                    raise
                last_category = category
                last_retryable_error = exc

                exhausted_reason = self._exhausted_reason(attempt, deadline)
                if exhausted_reason is not None:
                    on_exhausted(category, exhausted_reason)
                    logger.warning(
                        "[LLM] retry exhausted model=%s provider=%s attempt=%d "
                        "category=%s exception=%s status=%s reason=%s",
                        model,
                        provider or "(inferred)",
                        attempt,
                        category,
                        type(exc).__name__,
                        getattr(exc, "status_code", None),
                        exhausted_reason,
                    )
                    raise

                delay = self._retry_delay(exc, category, attempt, deadline)
                if deadline - self._monotonic() <= 0:
                    on_exhausted(category, "deadline")
                    raise
                # Full jitter may intentionally select 0 seconds. Awaiting
                # sleep(0) yields to the event loop before the next attempt.
                on_retry(category, delay)
                logger.warning(
                    "[LLM] retrying model=%s provider=%s attempt=%d category=%s "
                    "exception=%s status=%s retry_after_present=%s delay_seconds=%.3f",
                    model,
                    provider or "(inferred)",
                    attempt,
                    category,
                    type(exc).__name__,
                    getattr(exc, "status_code", None),
                    retry_after_seconds(exc) is not None,
                    delay,
                )
                await self._sleep(delay)

        raise AssertionError("retry loop must return or raise")

    def _exhausted_reason(self, attempt: int, deadline: float) -> str | None:
        if attempt >= self._policy.max_attempts:
            return "attempt_limit"
        if deadline - self._monotonic() <= 0:
            return "deadline"
        return None

    def _retry_delay(
        self,
        exc: Exception,
        category: RetryCategory,
        attempt: int,
        deadline: float,
    ) -> float:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            return 0.0
        server_delay = retry_after_seconds(exc)
        if server_delay is not None:
            return min(server_delay, remaining)
        base = (
            self._policy.throttled_base_seconds
            if category == "throttled"
            else self._policy.transient_base_seconds
        )
        ceiling = min(
            self._policy.max_backoff_seconds,
            base * (2 ** (attempt - 1)),
            remaining,
        )
        return self._uniform(0.0, ceiling)
