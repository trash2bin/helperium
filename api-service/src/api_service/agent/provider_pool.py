"""ProviderPool — пул здоровых LLM-провайдеров.

Заменяет create_fallback_client() + Router целиком.
Не использует litellm.Router.

Каждый ``ProviderWorker`` оборачивает ``LiteLLMProvider`` с health check.
Пул выбирает живого воркера через round-robin и поддерживает
``complete_with_fallback`` — пробует воркеров по очереди при ошибках.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

from .litellm_provider import LiteLLMProvider
from .models import CompletionRequest, CompletionResponse

logger = logging.getLogger("api_service.agent.provider_pool")

_HEALTH_INTERVAL_S: float = 30.0
_HEALTH_TIMEOUT_S: float = 5.0
_HEALTH_PATH: str = "/health"


@dataclass(slots=True)
class ProviderWorker:
    """Один провайдер с health check.

    Wraps a ``LiteLLMProvider`` and tracks its last-known health status.
    """

    name: str
    model: str
    api_base: str
    provider_impl: LiteLLMProvider
    _last_healthy: float = 0.0
    """Timestamp (monotonic) of the last successful health check.  0 = unknown."""

    _consecutive_failures: int = 0
    """Number of consecutive health-check failures."""

    def __repr__(self) -> str:
        return f"<ProviderWorker {self.name}:{self.model}>"

    async def health(self) -> bool:
        """Check whether this provider is reachable.

        Sends a HEAD request to the provider's ``/health`` endpoint with
        a short timeout.  Returns ``True`` if the response status is < 500.
        """
        if not self.api_base:
            # No API base → can't health-check; assume alive.
            self._last_healthy = _monotonic()
            self._consecutive_failures = 0
            return True

        url = self.api_base.rstrip("/") + _HEALTH_PATH
        try:
            async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT_S) as client:
                resp = await client.head(url)
                alive = resp.status_code < 500
        except (httpx.HTTPError, OSError) as exc:
            logger.debug(
                "[POOL] Health check failed for %s (%s): %s",
                self.name,
                url,
                exc,
            )
            alive = False

        if alive:
            self._last_healthy = _monotonic()
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1

        return alive

    @property
    def is_alive(self) -> bool:
        """Quick check: was the worker healthy recently?"""
        if not self.api_base:
            return True  # no known base → assume alive
        if self._last_healthy == 0:
            return True  # never checked → assume alive
        age = _monotonic() - self._last_healthy
        return age < _HEALTH_INTERVAL_S * 3 and self._consecutive_failures < 3

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        """Delegate to the underlying provider."""
        return await self.provider_impl.complete(req)


def _monotonic() -> float:
    """Return monotonic clock seconds (helper for testability)."""
    import time

    return time.monotonic()


class ProviderPool:
    """Пул с фоновым health check и round-robin.

    Usage::

        pool = ProviderPool()
        pool.add_worker(name="mistral", model="mistral/mistral-small", ...)

        # On every request:
        resp = await pool.complete_with_fallback(req)

    Workers are checked in the background every ``_HEALTH_INTERVAL_S``
    seconds.  ``complete_with_fallback`` tries workers in a round-robin
    order, skipping unhealthy ones, and falls back to the next on error.
    """

    def __init__(self) -> None:
        self._workers: dict[str, ProviderWorker] = {}
        self._rr_index: int = 0
        self._lock = asyncio.Lock()
        self._health_task: asyncio.Task | None = None
        self._health_started: bool = False

    # ── Worker management ──────────────────────────────────────────────

    def add_worker(
        self,
        name: str,
        model: str,
        api_base: str = "",
        api_key: str = "",
        timeout: float = 120.0,
        temperature: float = 0.5,
        max_tokens_thinking: int = 4096,
        enable_thinking: bool = False,
    ) -> ProviderWorker:
        """Add or replace a worker in the pool.

        Returns the created ``ProviderWorker``.
        """
        provider = LiteLLMProvider(
            model=model,
            api_base=api_base or None,
            timeout=timeout,
            temperature=temperature,
            max_tokens_thinking=max_tokens_thinking,
            enable_thinking=enable_thinking,
        )
        worker = ProviderWorker(
            name=name,
            model=model,
            api_base=api_base,
            provider_impl=provider,
        )
        self._workers[name] = worker
        logger.info("[POOL] Added worker: %s (%s)", name, model)
        return worker

    def remove_worker(self, name: str) -> bool:
        """Remove a worker by name. Returns ``True`` if it existed."""
        if name in self._workers:
            del self._workers[name]
            logger.info("[POOL] Removed worker: %s", name)
            return True
        return False

    async def alive_workers(self) -> list[ProviderWorker]:
        """Return workers that are currently considered alive."""
        async with self._lock:
            return [w for w in self._workers.values() if w.is_alive]

    async def pick(self) -> ProviderWorker:
        """Round-robin pick of an alive worker.

        Raises:
            RuntimeError: if no alive workers are available.
        """
        alive = await self.alive_workers()
        if not alive:
            raise RuntimeError("No alive providers in pool")

        async with self._lock:
            if self._rr_index >= len(alive):
                self._rr_index = 0
            worker = alive[self._rr_index]
            self._rr_index = (self._rr_index + 1) % len(alive)
        return worker

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        """Call the next alive provider (round-robin)."""
        worker = await self.pick()
        logger.debug("[POOL] Picked worker %s for completion", worker.name)
        return await worker.complete(req)

    async def complete_with_fallback(
        self,
        req: CompletionRequest,
    ) -> CompletionResponse:
        """Try alive workers in order until one succeeds.

        If the first picked worker fails, falls back to the next alive
        worker, etc.  Raises if all workers fail.
        """
        alive = await self.alive_workers()
        if not alive:
            raise RuntimeError("No alive providers in pool for fallback")

        # Shift to a random start for even distribution
        async with self._lock:
            start = self._rr_index % len(alive)
            self._rr_index = (start + 1) % len(alive)

        errors: list[tuple[str, str]] = []
        for i in range(len(alive)):
            idx = (start + i) % len(alive)
            worker = alive[idx]
            try:
                return await worker.complete(req)
            except Exception as exc:
                logger.warning("[POOL] Worker %s failed: %s", worker.name, exc)
                errors.append((worker.name, str(exc)))

        raise RuntimeError(f"All {len(alive)} providers failed: {errors}")

    # ── Background health check ────────────────────────────────────────

    def start_health_checks(self) -> None:
        """Start the background health-check loop (idempotent)."""
        if self._health_started:
            return
        self._health_started = True
        self._health_task = asyncio.create_task(self._health_loop())
        logger.info("[POOL] Health checks started")

    async def stop_health_checks(self) -> None:
        """Cancel the health-check loop."""
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
        self._health_started = False
        logger.info("[POOL] Health checks stopped")

    async def _health_loop(self) -> None:
        """Periodically check every worker."""
        while True:
            await asyncio.sleep(_HEALTH_INTERVAL_S)
            async with self._lock:
                workers = list(self._workers.values())
            for w in workers:
                try:
                    await w.health()
                except Exception as exc:
                    logger.warning(
                        "[POOL] Health check exception for %s: %s",
                        w.name,
                        exc,
                    )
            alive = sum(1 for w in workers if w.is_alive)
            logger.debug(
                "[POOL] Health check round: %d/%d alive",
                alive,
                len(workers),
            )

    def worker_names(self) -> list[str]:
        """Return all worker names (for inspection)."""
        return list(self._workers.keys())
