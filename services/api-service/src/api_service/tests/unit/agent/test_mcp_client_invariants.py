"""Инвариантный сьют MCPClient — единственный источник правды.

Спека: ревью-протокол «тесты как единственный источник правды» (round-7).
Каждый тест покрывает ровно один инвариант и обязан краснеть при
хирургическом откате соответствующего фикса. Протокол красной проверки:

    1) Пишем/мигрируем тест  -> ЗЕЛЁНЫЙ
    2) Откатываем фикс хирургически -> ОБЯЗАН краснеть с осмысленной ошибкой
    3) Восстанавливаем фикс -> ЗЕЛЁНЫЙ

Если шаг 2 не краснеет — тест декоративный, его не сдаём.

Карта «тест ↔ фикс ↔ наблюдаемая ошибка при откате» зафиксирована
в докстринге каждого теста («RED если»). Сводная таблица живёт в
отчёте воркера (см. репозиторий-уровневый комментарий коллеги).

Группы:
    1. anyio one-task rule (open/close в одной таске владельца)
    2. breaker на трёх путях в сеть: cold / hot / reconnect
    3. hard deadline, когда SDK глотает отмену: call_tool / list_tools
    4. разнесённые бюджеты lock_acquire vs execution
    5. zombie-quarantine после N таймаутов подряд
    6. bounded close: верхний кап + форс-закрытие транспорта
    7. изоляция тенантов (нет HOL-блокировки)
    8. дискриминация browser-disconnect vs SDK-blip (оба сайта)

Файл самодостаточен: собственные импорты и хелперы, ничего из соседнего
test_mcp_client.py не переиспользуется.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api_service.agent import mcp_client as mcp_client_module
from api_service.agent.mcp_client import (
    MCPClient,
    _CircuitBreakerOpen,
    _SessionProxy,
    _TenantConnection,
)
from helperium_sdk.settings import settings


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_conn() -> MagicMock:
    """Build a mock _TenantConnection with a mock session.

    Both ``call_lock`` and ``list_lock`` use ``acquire()`` / ``release()``
    (split-budget protocol), not ``async with``.
    """
    conn = MagicMock()
    conn.tenant_id = "test-tenant"
    conn.session = AsyncMock()
    conn.call_lock = MagicMock()
    conn.call_lock.acquire = AsyncMock(return_value=True)
    conn.call_lock.release = MagicMock()
    conn.list_lock = MagicMock()
    conn.list_lock.acquire = AsyncMock(return_value=True)
    conn.list_lock.release = MagicMock()
    conn.consecutive_tool_timeouts = 0
    return conn


def _mock_tool(
    name: str, description: str, input_schema: dict | None = None
) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.input_schema = input_schema or {"type": "object", "properties": {}}
    return tool


def _mock_result(content_parts: list[dict], is_error: bool = False) -> MagicMock:
    result = MagicMock()
    result.content = []
    for part in content_parts:
        block = MagicMock()
        block.type = part.get("type", "text")
        block.text = part.get("text", "")
        result.content.append(block)
    result.is_error = is_error
    return result


@pytest.fixture
def mcp_client() -> MCPClient:
    return MCPClient()


# ── Helpers for cancel-suppressing SDK simulations ───────────────────────────


def _suppressing_call_tool_factory():
    """Возвращает корутину, которая глотает CancelledError как SDK-стиль.

    while-цикл со sleep(3600) внутри try/except CancelledError — репродукция
    Streamable HTTP receiver'а на разрыве upstream-соединения. Хелпер-каунтер
    swallows показывает, что runner действительно дёрнул cancel хотя бы раз.

    ВАЖНО (урок red-протокола): цикл ОГРАНИЧЕН (max_swallows). Бесконечный
    swallow рождает неубиваемую таску, которая переживает event-loop и вешает
    pytest-asyncio teardown — сам прод-код при этом отработал правильно.
    """
    state = {"swallows": 0}

    async def hanging(*args, **kwargs):
        while state["swallows"] < 5:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                state["swallows"] += 1
                continue

    hanging._state = state  # type: ignore[attr-defined]
    return hanging


def _suppressing_list_tools_factory():
    """Аналогично для list_tools()."""
    state = {"swallows": 0}

    async def hanging(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            state["swallows"] += 1
            await asyncio.sleep(3600)

    hanging._state = state  # type: ignore[attr-defined]
    return hanging


# ═════════════════════════════════════════════════════════════════════════════
#  Группа 1 — правило одной таски (anyio)
# ═════════════════════════════════════════════════════════════════════════════


# ═════════════════════════════════════════════════════════════════════════════
#  Группа 1 — правило одной таски (anyio)
# ═════════════════════════════════════════════════════════════════════════════


# ═════════════════════════════════════════════════════════════════════════════
#  Группа 1 — правило одной таски (anyio)
# ═════════════════════════════════════════════════════════════════════════════


class TestMCPClientInvariants:
    """Production-level invariant suite — единственный источник правды.

    Импортировано из коллеги-ревью: «тесты как единственный источник правды».
    Сьют закрывает все 8 групп регрессионных инвариантов mcp_client.py.
    """

    # ────────────────────────────────────────────────────────────────────────
    #  Группа 1 — правило одной таски (anyio)
    # ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_open_and_close_run_in_same_owner_task(self):
        """Г1: __aenter__ и __aexit__ выполняются в ОДНОЙ таске (правило anyio).

        RED если: убрать spawn_owner (owner-task) и делать inline __aenter__
        и __aexit__ в тасках вызывающих — получим прямой CPU-спин из-за
        «Attempted to exit cancel scope in a different task».
        """
        seen: list[asyncio.Task | None] = []

        class TrackingCtx:
            async def __aenter__(self):
                seen.append(asyncio.current_task())
                return MagicMock()

            async def __aexit__(self, *exc):
                seen.append(asyncio.current_task())
                return False

        conn = _TenantConnection(
            tenant_id="v-same-task",
            session=None,
            session_ctx=TrackingCtx(),
            transport_http_client=None,
        )
        conn.spawn_owner()
        await asyncio.wait_for(conn.wait_opened(), timeout=2)

        # Close ДРУГОЙ таской — ровно как из GC-лупа / quarantine.
        closer = asyncio.create_task(conn.close())
        await asyncio.wait_for(closer, timeout=5)

        assert len(seen) == 2, (
            f"TEST GAP: ожидались оба хука (__aenter__/__aexit__), "
            f"зафиксировано {len(seen)}"
        )
        assert seen[0] is not None and seen[0] is seen[1], (
            "REGRESSION (Г1): open и close выполнились в разных тасках — "
            "вернулся сценарий 'Attempted to exit cancel scope in a different task'"
        )

    # ────────────────────────────────────────────────────────────────────────
    #  Группа 2 — circuit breaker на трёх путях в сеть
    # ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_cold_path_breaker_fast_fails(self, mcp_client: MCPClient):
        """Г2-cold: реестр пуст, но брейкер открыт → fast-fail БЕЗ хендшейка.

        RED если: убрать проверку брейкера в начале _get_connection (до
        реестра) — каждый запрос будет платить полный mcp_session_init_timeout
        пока gateway мёртв.
        """
        mcp_client._store_breaker(
            "dead", settings.mcp_max_consecutive_failures, time.monotonic()
        )
        mcp_client._open_connection = AsyncMock()  # type: ignore[method-assign]
        with pytest.raises(_CircuitBreakerOpen):
            await mcp_client._get_connection(["dead"])
        mcp_client._open_connection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_hot_path_breaker_fast_fails(self, mcp_client: MCPClient):
        """Г2-hot: тёплый конн в реестре + открытый брейкер → fast-fail.

        RED если: убрать проверку брейкера внутри warm-ветки. Замечание:
        из-за двойного предохранителя (cold-precheck ДО лока + warm-проверка
        ВНУТРИ лока) одиночный откат warm-блока может оставаться зелёным
        за счёт cold-precheck. Для детерминированного RED требуется откат
        ОБОИХ предохранителей — это честная двойная защита, а не декорация.
        Если N1 краснеет только при комбинированном откате, отметь в отчёте.
        """
        warm = _TenantConnection(
            tenant_id="t", session=MagicMock(), session_ctx=MagicMock()
        )
        warm.last_used = time.monotonic()
        mcp_client._connections["t"] = warm
        mcp_client._store_breaker(
            "t", settings.mcp_max_consecutive_failures, time.monotonic()
        )
        with pytest.raises(_CircuitBreakerOpen):
            await mcp_client._get_connection(["t"])

    @pytest.mark.asyncio
    async def test_reconnect_respects_open_breaker(self, mcp_client: MCPClient):
        """Г2-reconnect: _reconnect не прёт в сеть при открытом контуре.

        RED если: убрать проверку брейкера в начале _reconnect — каждый
        retry полетит в мёртвый gateway на 15с вместо мгновенного fast-fail.
        """
        mcp_client._store_breaker(
            "t9", settings.mcp_max_consecutive_failures, time.monotonic()
        )
        mcp_client._open_connection = AsyncMock()  # type: ignore[method-assign]

        with pytest.raises(_CircuitBreakerOpen):
            await mcp_client._reconnect(["t9"])
        mcp_client._open_connection.assert_not_awaited()

    # ────────────────────────────────────────────────────────────────────────
    #  Группа 3 — hard deadline при SDK, глотающем отмену
    # ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_tool_call_returns_by_deadline_when_sdk_suppresses_cancellation(
        self, mcp_client: MCPClient, monkeypatch: pytest.MonkeyPatch
    ):
        """Г3-tool: call_tool возвращает ok=False за дедлайн, даже если SDK
        глотает CancelledError. Главный тест против 100%-CPU спина.

        RED если: заменить _run_with_hard_deadline на asyncio.wait_for —
        wait_for ждёт завершения отменённой таски; глотающая корутина
        продолжает крутиться → внешний watchdog 2с убивает тест с TimeoutError.
        """
        monkeypatch.setattr(
            mcp_client_module.settings, "mcp_tool_execution_timeout", 0.3
        )
        # Понижаем порог карантина далеко, чтобы за один прогон не сработал
        # (защищаем фокус именно на дедлайне, а не на эскалации).
        monkeypatch.setattr(mcp_client_module.settings, "mcp_zombie_tool_timeouts", 999)

        hanging = _suppressing_call_tool_factory()
        conn = _make_conn()
        conn.session.call_tool = AsyncMock(side_effect=hanging)
        mcp_client._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]

        session = _SessionProxy(mcp_client, tenant_ids=["suppress-tool"])
        t0 = time.monotonic()
        tr = await asyncio.wait_for(
            mcp_client.call_tool(session, "db_search", {}), timeout=2.0
        )
        elapsed = time.monotonic() - t0

        assert tr.ok is False, (
            f"REGRESSION (Г3-tool): call_tool не отдал ошибку, вернул ok={tr.ok}"
        )
        assert "timed out" in tr.error.lower(), f"неожиданный error: {tr.error!r}"
        assert elapsed < 1.5, (
            f"REGRESSION (Г3-tool): call_tool превысил execution-budget "
            f"({elapsed:.2f}s)"
        )

        # Урок red-протокола: бесконечный swallow рождает неубиваемую
        # detached-таску. Повтор-cancel с await до завершения — это часть
        # контракта теста (сам runner детачит таску честно).
        for _ in range(10):
            await asyncio.sleep(0)
            for t in asyncio.all_tasks():
                if t is not asyncio.current_task() and not t.done():
                    t.cancel()
        await asyncio.gather(
            *(t for t in asyncio.all_tasks() if t is not asyncio.current_task()),
            return_exceptions=True,
        )
        # Глотатель реально получил хотя бы одну отмену — иначе тест
        # не нагрузил код-путь, который мы защищаем.
        assert hanging._state["swallows"] >= 1, (
            "подменная SDK-корутина не дождалась cancel от runner'а"
        )

    @pytest.mark.asyncio
    async def test_list_tools_returns_empty_by_deadline_when_sdk_suppresses_cancellation(
        self, mcp_client: MCPClient, monkeypatch: pytest.MonkeyPatch
    ):
        """Г3-list: list_tools возвращает [] за дедлайн при SDK-стиль глотании отмены.

        RED если: вернуть asyncio.wait_for в _list_tools_bounded — будет
        виснуть вечно, внешний watchdog 5с убьёт тест с TimeoutError.
        """
        monkeypatch.setattr(
            mcp_client_module.settings, "mcp_tool_execution_timeout", 0.3
        )

        hanging = _suppressing_list_tools_factory()
        conn = MagicMock()
        conn.tenant_id = "lt-zombie"
        conn.list_lock = asyncio.Lock()
        conn.last_used = time.monotonic()
        conn.session = MagicMock()
        conn.session.list_tools = AsyncMock(side_effect=hanging)
        mcp_client._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]

        session = _SessionProxy(mcp_client, tenant_ids=["lt-zombie"])
        t0 = time.monotonic()
        tools = await asyncio.wait_for(mcp_client.list_tools(session), timeout=5.0)
        elapsed = time.monotonic() - t0

        assert tools == [], f"REGRESSION: hung listing returned {tools!r}"
        assert elapsed < 2.0, (
            f"REGRESSION: listing exceeded execution budget ({elapsed:.2f}s)"
        )

        # Дать event loop доставить cancel в детачнутую таску (swallows
        # растёт асинхронно), затем добить и дождаться — иначе teardown
        # pytest-asyncio виснет на неубиваемой sleep(3600) корутине.
        for _ in range(10):
            await asyncio.sleep(0)
            for t in asyncio.all_tasks():
                if t is not asyncio.current_task() and not t.done():
                    t.cancel()
        await asyncio.gather(
            *(t for t in asyncio.all_tasks() if t is not asyncio.current_task()),
            return_exceptions=True,
        )
        assert hanging._state["swallows"] >= 1, (
            "подменная SDK-корутина не дождалась cancel от runner'а "
            f"(swallows={hanging._state['swallows']})"
        )

    # ────────────────────────────────────────────────────────────────────────
    #  Группа 4 — разнесённые бюджеты lock_acquire ≠ execution
    # ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    @patch("helperium_sdk.settings.settings.mcp_lock_acquire_timeout", 0.1)
    @patch("helperium_sdk.settings.settings.mcp_tool_execution_timeout", 1.5)
    async def test_tool_runs_longer_than_lock_budget(self, mcp_client: MCPClient):
        """Г4-tool: тул живёт дольше lock_acquire_timeout, но меньше exec — успевает.

        RED если: обернуть лок+исполнение одним asyncio.timeout(lock_timeout)
        → тул умрёт на 0.1с с сообщением «Tool call timed out».
        """
        conn = _make_conn()

        async def slow_tool(name, arguments):
            await asyncio.sleep(0.4)
            return _mock_result([{"type": "text", "text": "done"}])

        conn.session.call_tool = AsyncMock(side_effect=slow_tool)
        mcp_client._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]

        session = _SessionProxy(mcp_client, tenant_ids=[])
        tr = await mcp_client.call_tool(session, "slow_query", {})

        assert tr.ok is True, (
            f"REGRESSION (Г4-tool): tool killed by shared budget: {tr.error!r}"
        )
        assert "done" in tr.tool_content

    @pytest.mark.asyncio
    @patch("helperium_sdk.settings.settings.mcp_lock_acquire_timeout", 0.1)
    @patch("helperium_sdk.settings.settings.mcp_tool_execution_timeout", 1.5)
    async def test_listing_runs_longer_than_lock_budget(self, mcp_client: MCPClient):
        """Г4-list: листинг 0.4с > лок-бюджет 0.1с, но < exec-бюджет 1.5с — успевает.

        RED если: один asyncio.timeout накрывает и acquire(), и сам вызов —
        listing молча деградирует в [] с вводящим в заблуждение логом
        «timed out waiting for list lock».
        """
        conn = MagicMock()
        conn.tenant_id = "lt-split"
        conn.list_lock = asyncio.Lock()
        conn.last_used = time.monotonic()

        async def slow_listing():
            await asyncio.sleep(0.4)
            return MagicMock(tools=[_mock_tool("db_search", "Search")])

        conn.session = AsyncMock()
        conn.session.list_tools = AsyncMock(side_effect=slow_listing)
        mcp_client._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]

        session = _SessionProxy(mcp_client, tenant_ids=["lt-split"])
        tools = await mcp_client.list_tools(session)

        assert len(tools) == 1, (
            f"REGRESSION (Г4-list): listing killed by shared budget: {tools!r}"
        )
        assert tools[0]["function"]["name"] == "db_search"

    @pytest.mark.asyncio
    async def test_lock_wait_uses_lock_budget_not_execution_budget(
        self, mcp_client: MCPClient
    ):
        """Г4-lock-wait: конкуренция за list_lock гасится КОРОТКИМ бюджетом.

        RED если: один asyncio.timeout(mcp_tool_execution_timeout) накроет
        и ожидание лока, и листинг — тест проедет 1.5с вместо 0.15с.
        """
        conn = MagicMock()
        conn.tenant_id = "lt-lock"
        conn.list_lock = asyncio.Lock()
        conn.last_used = time.monotonic()
        await conn.list_lock.acquire()  # кто-то держит лок надолго

        conn.session = AsyncMock()
        mcp_client._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]

        with patch("helperium_sdk.settings.settings.mcp_lock_acquire_timeout", 0.15):
            t0 = time.monotonic()
            tools = await mcp_client.list_tools(
                _SessionProxy(mcp_client, tenant_ids=["lt-lock"])
            )
            elapsed = time.monotonic() - t0

        assert tools == []  # санитарная деградация сохранена
        assert elapsed < 1.0, (
            f"REGRESSION (Г4-lock-wait): lock wait ran on execution budget "
            f"({elapsed:.2f}s)"
        )

    # ────────────────────────────────────────────────────────────────────────
    #  Группа 5 — zombie quarantine (эскалация после N таймаутов)
    # ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_consecutive_timeouts_trigger_quarantine(
        self,
        mcp_client: MCPClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Г5: после mcp_zombie_tool_timeouts подряд таймаутов коннект карантируется.

        RED если: убрать блок эскалации в except TimeoutError внутри
        _execute_tool_call — счётчик не растёт, _quarantine_connection
        никогда не вызывается, mcp_connection_quarantines_total не растёт.
        """
        monkeypatch.setattr(
            mcp_client_module.settings, "mcp_tool_execution_timeout", 0.05
        )
        monkeypatch.setattr(mcp_client_module.settings, "mcp_zombie_tool_timeouts", 2)

        # Создаём настоящий _TenantConnection, чтобы spawn_owner/wait_opened
        # работали; session — MagicMock, call_tool вешаем через подмену.
        conn = _TenantConnection(
            tenant_id="z1",
            session=MagicMock(),
            session_ctx=MagicMock(),
            transport_http_client=MagicMock(),
        )
        conn.spawn_owner()
        await asyncio.wait_for(conn.wait_opened(), timeout=2)

        async def hanging_call(*args, **kwargs):
            await asyncio.Event().wait()  # никогда не завершается

        conn.session.call_tool = hanging_call  # type: ignore[assignment]

        # Spy на карантин и метрику.
        quarantine_spy = AsyncMock()
        mcp_client._quarantine_connection = quarantine_spy  # type: ignore[assignment]
        metric_before = mcp_client_module.mcp_connection_quarantines_total._value.get()

        mcp_client._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]
        session = _SessionProxy(mcp_client, tenant_ids=["z1"])

        # Два вызова call_tool подряд: первый инкрементирует до 1 (< 2),
        # второй пересекает порог и запускает карантин.
        tr1 = await asyncio.wait_for(
            mcp_client.call_tool(session, "db_map", {}), timeout=5.0
        )
        assert tr1.ok is False
        assert conn.consecutive_tool_timeouts == 1

        tr2 = await asyncio.wait_for(
            mcp_client.call_tool(session, "db_map", {}), timeout=5.0
        )
        assert tr2.ok is False
        assert conn.consecutive_tool_timeouts >= 2

        # Карантин спавнится detached — даём циклу отработать.
        for _ in range(5):
            await asyncio.sleep(0)
        await asyncio.sleep(0.1)

        assert quarantine_spy.await_count >= 1, (
            f"REGRESSION (Г5): _quarantine_connection не вызван "
            f"(call_count={quarantine_spy.await_count})"
        )
        metric_after = mcp_client_module.mcp_connection_quarantines_total._value.get()
        assert metric_after >= metric_before + 1, (
            f"REGRESSION (Г5): mcp_connection_quarantines_total не вырос "
            f"({metric_before} → {metric_after})"
        )

    # ────────────────────────────────────────────────────────────────────────
    #  Группа 6 — bounded close (двухслойная защита)
    # ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_close_is_bounded_when_aexit_hangs(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Г6: close() возвращается сам, даже если __aexit__ спит 30с.

        Известный нюанс: однослойный откат остаётся ЗЕЛЁНЫМ (верхний кап
        в close() + нижний кап в _exit_session_bounded — двойная защита).
        RED требует отката ОБОИХ слоёв одновременно. Это честная двойная
        защита, не декорация: каждый слой по отдельности доказан в
        test_layer1_/test_layer2_ ниже.

        Дискриминатор против «теста-декорации»: exited_cleanly должен
        остаться несработавшим, иначе close вернулся потому, что __aexit__
        успел сам — и тест перестаёт доказывать форс-обрыв.
        """
        monkeypatch.setattr(
            mcp_client_module.settings, "mcp_close_escalation_timeout", 0.2
        )

        class HangingExitCtx:
            def __init__(self) -> None:
                self.entered = False
                self.exited_cleanly = asyncio.Event()

            async def __aenter__(self):
                self.entered = True
                return MagicMock()

            async def __aexit__(self, *exc):
                try:
                    await asyncio.sleep(30)
                    self.exited_cleanly.set()
                except asyncio.CancelledError:
                    pass

        ctx = HangingExitCtx()
        conn = _TenantConnection(
            tenant_id="b-bounded",
            session=None,
            session_ctx=ctx,
            transport_http_client=MagicMock(),
        )
        conn.transport_http_client.aclose = AsyncMock()  # type: ignore[method-assign]

        conn.spawn_owner()
        opened = await asyncio.wait_for(conn.wait_opened(), timeout=2)
        assert opened is not None and ctx.entered

        await asyncio.wait_for(conn.close(), timeout=2.0)

        assert not ctx.exited_cleanly.is_set(), (
            "TEST GAP: __aexit__ завершился естественно — "
            "тест больше не доказывает форс-обрыв по escalation"
        )
        conn.transport_http_client.aclose.assert_awaited()

    @pytest.mark.asyncio
    async def test_layer1_exit_session_bounded_forces_close(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Г6-layer1: _exit_session_bounded рвёт сокет по escalation.

        RED если: убрать async with asyncio.timeout(escalation) внутри
        _exit_session_bounded — голый await __aexit__ зависает, транспорт
        не закрывается. Тест использует собственный wait_for с большим
        лимитом, чтобы дискриминировать только нижний слой.
        """
        monkeypatch.setattr(
            mcp_client_module.settings, "mcp_close_escalation_timeout", 0.2
        )
        exited_cleanly = asyncio.Event()

        class HangingExitCtx:
            async def __aenter__(self):
                return MagicMock()

            async def __aexit__(self, *exc):
                try:
                    await asyncio.sleep(30)
                    exited_cleanly.set()
                except asyncio.CancelledError:
                    pass

        ctx = HangingExitCtx()
        conn = _TenantConnection(
            tenant_id="b-layer1",
            session=None,
            session_ctx=ctx,
            transport_http_client=MagicMock(),
        )
        conn.transport_http_client.aclose = AsyncMock()  # type: ignore[method-assign]

        # Дискриминатор — ВРЕМЯ, а не wait_for: на Python 3.12+ wait_for не
        # рвёт TimeoutError, если внутренняя корутина поглотила отмену и
        # вернулась. С эскалацией возврат ~0.2с; без неё отмена поглотится
        # и возврат придёт только на внешнем wait_for (~3с).
        t0 = time.monotonic()
        await asyncio.wait_for(conn._exit_session_bounded(), timeout=6.0)
        elapsed = time.monotonic() - t0

        assert elapsed < 1.5, (
            f"REGRESSION (Г6-layer1): эскалация не оборвала __aexit__ "
            f"({elapsed:.2f}s вместо ~0.2s)"
        )
        assert not exited_cleanly.is_set(), (
            "TEST GAP: __aexit__ завершился естественно — слоя эскалации нет"
        )
        conn.transport_http_client.aclose.assert_awaited()

    @pytest.mark.asyncio
    async def test_layer2_close_caps_owner_wait_when_owner_silent(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Г6-layer2: close() ограничен 3×escalation при молчащем owner.

        RED если: убрать total_wait кап в close() (asyncio.wait без timeout)
        — close() зависает навсегда. Тест не запускает owner; руками создаёт
        события, имитируя «owner не отвечает на _close_event».
        """
        monkeypatch.setattr(
            mcp_client_module.settings, "mcp_close_escalation_timeout", 0.2
        )
        conn = _TenantConnection(
            tenant_id="b-layer2",
            session=MagicMock(),
            session_ctx=MagicMock(),
            transport_http_client=None,
        )
        conn._close_event = asyncio.Event()
        conn._closed_done = asyncio.Event()

        await asyncio.wait_for(conn.close(), timeout=5.0)

        assert conn._close_event.is_set(), "close must have signaled the event"

    # ────────────────────────────────────────────────────────────────────────
    #  Группа 7 — изоляция тенантов (нет HOL-блокировки при медленном open)
    # ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_slow_open_does_not_block_cached_tenant(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Г7: зависший open('slow') не блокирует cache-hit('fast').

        Патчится ТОЛЬКО транспорт: httpx2.AsyncClient заменяется объектом,
        чей stream() никогда не возвращается. Весь open-путь (_registry_lock,
        spawn_owner, wait_opened) остаётся настоящим — если фикс 7 откатить
        (открытие снова под локом), ожидание cache-hit падает по TimeoutError.

        Сетевой вариант теста (TCP-чёрная дыра на 127.0.0.1) отвергнут:
        владелец с живым сокетом протекал между тестами и подвешивал класс.
        """
        client = MCPClient()

        class HangingTransport:
            def __init__(self) -> None:
                self.aclose = AsyncMock()
                self._never = asyncio.Event()

            def stream(self, *a: object, **kw: object) -> "HangingStream":
                return HangingStream(self._never)

        class HangingStream:
            def __init__(self, never: asyncio.Event) -> None:
                self._never = never

            async def __aenter__(self):
                await self._never.wait()
                raise AssertionError("unreachable: hang never releases")

            async def __aexit__(self, *exc):
                return False

        def make_hanging_client(*a: object, **kw: object) -> HangingTransport:
            return HangingTransport()

        monkeypatch.setattr(mcp_client_module.settings, "mcp_session_init_timeout", 5.0)

        warm_conn = _TenantConnection(
            tenant_id="fast", session=MagicMock(), session_ctx=MagicMock()
        )
        warm_conn.last_used = time.monotonic()
        client._connections["fast"] = warm_conn

        with patch(
            "api_service.agent.mcp_client.httpx2.AsyncClient",
            side_effect=make_hanging_client,
        ):
            slow_task = asyncio.create_task(client._get_connection(["slow"]))
            try:
                await asyncio.sleep(0.2)
                assert not slow_task.done(), (
                    "TEST GAP: медленный handshake завершился сам — "
                    "подмена транспорта не сработала"
                )

                got_fast = await asyncio.wait_for(
                    client._get_connection(["fast"]), timeout=1.0
                )
                assert got_fast is warm_conn, (
                    "REGRESSION (Г7): 'fast' получил не свой кэшированный коннект"
                )
            finally:
                slow_task.cancel()
                with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(slow_task), timeout=3)
                await asyncio.sleep(0)
                await asyncio.sleep(0)

    # ────────────────────────────────────────────────────────────────────────
    #  Группа 8 — дискриминация browser-disconnect vs SDK-blip (оба сайта)
    # ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_real_client_disconnect_reraises(self, mcp_client: MCPClient):
        """Г8-site-1 (первая попытка): клиент ушёл → CancelledError пробрасывается.

        RED если: убрать `if client_gone: raise` в первом except asyncio.CancelledError
        блоке call_tool — браузер отвалился, а агент продолжает жечь
        провайдер-раунды и записывает невидимый tool turn в транскрипт.
        """
        conn = _make_conn()
        conn.session.call_tool = AsyncMock(side_effect=asyncio.CancelledError())
        mcp_client._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]

        session = _SessionProxy(mcp_client, tenant_ids=[])
        session.disconnect_check = lambda: True

        with pytest.raises(asyncio.CancelledError):
            await mcp_client.call_tool(session, "db_search", {})

    @pytest.mark.asyncio
    async def test_sdk_blip_returns_retryable(self, mcp_client: MCPClient):
        """Г8-blip: SDK-блip без дисконнекта → retryable + uncancel().

        Реалистичная механика: SDK отменяет САМУ таску-владельца (родителя),
        а не кидает CancelledError из дочерней корутины. Только тогда
        parent.cancelling() > 0 и ветка uncancel() вообще живая.

        Дискриминатор против «теста-декорации»: без current_task.uncancel()
        счётчик cancelling() остаётся >0 после возврата — любой код после
        call_tool увидит «отменённую» таску и аварийно упадёт.

        RED если: убрать current_task.uncancel() — assert cancelling()==0
        после возврата краснеет.
        """
        conn = _make_conn()
        parent_task = asyncio.current_task()
        assert parent_task is not None

        async def sdk_cancels_caller(*args, **kwargs):
            # Точная эмуляция Streamable HTTP SDK: он отменяет таску,
            # из которой был вызван call_tool (нашу — родительскую).
            parent_task.cancel()
            await asyncio.sleep(30)  # child продолжает жить, cancel вернётся в родителя

        conn.session.call_tool = sdk_cancels_caller
        mcp_client._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]

        session = _SessionProxy(mcp_client, tenant_ids=[])
        session.disconnect_check = None  # SDK-blip, не реальный дисконнект

        tr = await mcp_client.call_tool(session, "db_search", {})
        assert tr.ok is False
        assert tr.error == "Tool connection was interrupted; retry shortly."
        assert parent_task.cancelling() == 0, (
            "REGRESSION (Г8-blip): uncancel() не выполнился — "
            f"task.cancelling()={parent_task.cancelling()}"
        )

        # Cleanup: детачнутая дочерняя таска могла выжить — гасим.
        for _ in range(10):
            await asyncio.sleep(0)
            for t in asyncio.all_tasks():
                if t is not asyncio.current_task() and not t.done():
                    t.cancel()
        await asyncio.gather(
            *(t for t in asyncio.all_tasks() if t is not asyncio.current_task()),
            return_exceptions=True,
        )

    @pytest.mark.asyncio
    async def test_retry_arm_disconnect_reraises(self, mcp_client: MCPClient):
        """Г8-site-2 (retry-arm после reconnect): клиент ушёл → CancelledError пробрасывается.

        Первая попытка → Exception → reconnect-ветка. Подменяем _reconnect
        свежим конном, чей session.call_tool кидает CancelledError. С
        disconnect_check=True второй except asyncio.CancelledError должен
        raise, а не возвращать retryable.

        RED если: убрать `if client_gone: raise` во втором except
        asyncio.CancelledError (retry-arm).
        """
        first_conn = _make_conn()
        first_conn.session.call_tool = AsyncMock(side_effect=ValueError("boom"))
        retry_conn = _make_conn()
        retry_conn.session.call_tool = AsyncMock(side_effect=asyncio.CancelledError())
        mcp_client._get_connection = AsyncMock(return_value=first_conn)  # type: ignore[method-assign]
        mcp_client._reconnect = AsyncMock(return_value=retry_conn)  # type: ignore[method-assign]

        session = _SessionProxy(mcp_client, tenant_ids=[])
        session.disconnect_check = lambda: True

        with pytest.raises(asyncio.CancelledError):
            await mcp_client.call_tool(session, "db_search", {})
