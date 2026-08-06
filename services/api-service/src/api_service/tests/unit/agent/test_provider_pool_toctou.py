"""TDD tests: ProviderPool TOCTOU race между alive_workers() и pick().

Проблема: remove_worker() НЕ использует _lock, в отличие от alive_workers()
и pick(). Это позволяет гонке: remove_worker() может удалить воркера
в момент между тем как alive_workers() прочитал список и pick() взял _lock.

Дополнительно: _rr_index не обновляется при remove_worker().

Тесты ПАДАЮТ пока фикс не внедрён.
"""

from __future__ import annotations

import asyncio

import pytest

from api_service.agent.provider_pool import ProviderPool


def _make_pool(worker_count: int = 3) -> ProviderPool:
    """Create a pool with N workers that have no api_base (-> always alive)."""
    pool = ProviderPool()
    for i in range(worker_count):
        pool.add_worker(
            name=f"worker-{i}",
            model=f"test-model-{i}",
            api_base="",
        )
    return pool


class TestRemoveWorkerLocking:
    """remove_worker() должен брать _lock для атомарности."""

    @pytest.mark.asyncio
    async def test_remove_worker_should_acquire_lock(self):
        """remove_worker() ДОЛЖЕН брать _lock (сейчас не берёт — баг)."""
        pool = _make_pool(3)
        lock_was_held = False

        original_acquire = pool._lock.acquire

        async def tracking_acquire():
            nonlocal lock_was_held
            lock_was_held = True
            return await original_acquire()

        pool._lock.acquire = tracking_acquire  # type: ignore[method-assign]

        await pool.remove_worker("worker-0")

        assert lock_was_held, (
            "\n\n❌ TDD FAIL: remove_worker() НЕ взял _lock.\n"
            "remove_worker() может удалить воркера в момент когда "
            "pick() уже прочитал alive_workers().\n"
            "Фикс: async with self._lock внутри remove_worker()."
        )


class TestRRIndexAfterRemove:
    """_rr_index должен корректироваться при remove_worker()."""

    @pytest.mark.asyncio
    async def test_rr_index_updated_when_removing_before_current(self):
        """При удалении воркера ДО _rr_index, индекс должен уменьшиться."""
        pool = _make_pool(5)
        pool._rr_index = 3

        # Удаляем worker-1 (перед _rr_index)
        await pool.remove_worker("worker-1")

        assert pool._rr_index == 2, (
            "\n\n❌ TDD FAIL: _rr_index не скорректирован после удаления воркера ДО него.\n"
            f"Было: 3, удалили worker-1 (индекс 1), стало: {pool._rr_index}, ожидалось: 2.\n"
            "Когда удалён воркер с индексом < _rr_index, индекс нужно уменьшить на 1."
        )

    @pytest.mark.asyncio
    async def test_rr_index_clamped_when_removing_at_current(self):
        """При удалении воркера на котором _rr_index, индекс должен стать 0."""
        pool = _make_pool(5)
        pool._rr_index = 3

        # Удаляем worker-3 (на котором _rr_index)
        await pool.remove_worker("worker-3")

        assert pool._rr_index < len(pool._workers), (
            "\n\n❌ TDD FAIL: _rr_index не скорректирован после удаления воркера на котором он был.\n"
            f"Было: 3 (из 5), удалили worker-3, стало: {pool._rr_index}, "
            f"воркеров: {len(pool._workers)}\n"
            "_rr_index должен быть clamped в [0, len(_workers))."
        )

    @pytest.mark.asyncio
    async def test_rr_index_unchanged_when_removing_after_current(self):
        """При удалении воркера ПОСЛЕ _rr_index индекс не меняется."""
        pool = _make_pool(5)
        pool._rr_index = 1

        await pool.remove_worker("worker-3")  # после _rr_index

        assert pool._rr_index == 1, (
            "\n\n❌ TDD FAIL: _rr_index изменился при удалении воркера ПОСЛЕ него.\n"
            f"Было: 1, удалили worker-3 (индекс 3), стало: {pool._rr_index}\n"
            "При удалении воркера с индексом > _rr_index, индекс не должен меняться."
        )


class TestConcurrentRace:
    """Доказываем что remove_worker() может гоняться с pick()."""

    @pytest.mark.asyncio
    async def test_remove_worker_during_pick_causes_inconsistency(self):
        """remove_worker() может удалить воркера пока pick() в процессе.

        Сценарий:
        1. pick() → alive_workers() → snapshot [w0,w1,w2], отпустил _lock
        2. remove_worker("worker-0") без _lock — удалил w0
        3. pick() → async with self._lock → _rr_index может быть
           неконсистентным относительно нового списка
        """
        pool = _make_pool(3)
        entered_workers = asyncio.Event()
        remove_done = asyncio.Event()
        results: list[str] = []

        # Hook внутри alive_workers() — сигналим когда чтение сделано
        original_alive = pool.alive_workers

        async def hooked_alive():
            result = await original_alive()
            entered_workers.set()
            # Ждём пока remove_worker() выполнится
            await remove_done.wait()
            return result

        pool.alive_workers = hooked_alive  # type: ignore[method-assign]

        async def racer_remove():
            await entered_workers.wait()
            await pool.remove_worker("worker-0")
            results.append("removed")
            remove_done.set()

        async def racer_pick():
            try:
                worker = await pool.pick()
                results.append(f"picked:{worker.name}")
            except Exception as e:
                results.append(f"error:{e}")

        await asyncio.gather(racer_remove(), racer_pick())

        results_str = ", ".join(results)
        assert "removed" in results_str, (
            "\n\n❌ TDD FAIL: remove_worker() не выполнился конкурентно.\n"
            f"Результаты: {results_str}"
        )


class TestPoolWorksAfterFix:
    """После фикса pool должен корректно работать."""

    @pytest.mark.asyncio
    async def test_pool_normal_operation_after_fix(self):
        """Базовый сценарий: add + remove + pick + clear."""
        pool = _make_pool(3)

        w1 = await pool.pick()
        assert w1 is not None and w1.name == "worker-0"

        pool.add_worker(name="worker-new", model="new", api_base="")
        w2 = await pool.pick()
        assert w2 is not None

        await pool.remove_worker("worker-1")
        w3 = await pool.pick()
        assert w3 is not None

        await pool.clear()
        assert len(pool._workers) == 0

    @pytest.mark.asyncio
    async def test_pick_still_round_robins(self):
        """Round-robin не должен сломаться."""
        pool = _make_pool(3)

        picked = []
        for _ in range(4):
            w = await pool.pick()
            picked.append(w.name)

        assert picked == ["worker-0", "worker-1", "worker-2", "worker-0"], (
            f"Round-robin сломан: {picked}"
        )
