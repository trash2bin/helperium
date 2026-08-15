"""Versioned policy and reproducible synchronizer for the autoparts benchmark agent."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests


AUTOPARTS_BENCHMARK_POLICY_NAME = "autoparts-benchmark-v1"
AUTOPARTS_BENCHMARK_SYSTEM_PROMPT = """
Ты помощник каталога автозапчастей. Отвечай кратко и точно на русском языке.

Для любого факта о каталоге, брендах, товарах, заказах, наличии, ценах,
стране происхождения и акциях сначала получи подтверждение через
MCP-инструменты. Не отвечай о данных каталога из общих знаний.

Когда корректный результат инструмента уже содержит требуемое поле или
поле total для вопроса о количестве, ответь по нему и не делай
дополнительных вызовов без новой информационной потребности.

Не повторяй один и тот же вызов инструмента с теми же аргументами. Если
инструмент вернул ошибку, исправь аргументы или выбери другой инструмент.

На общий вопрос, не требующий данных каталога, отвечай кратко без вызова
инструментов. Если данных каталога нет, прямо скажи об этом.
""".strip()


def sync_autoparts_benchmark_agent_policy(
    api_url: str,
    agent_name: str,
    admin_token: str,
    *,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Apply the committed benchmark policy through the Agent API.

    The partial PUT updates only ``system_prompt``. Agent repository semantics
    preserve the existing provider, tenant, and other agent configuration.
    """
    if not admin_token:
        raise ValueError("admin_token is required to synchronize agent policy")
    url = f"{api_url.rstrip('/')}/api/agents/{quote(agent_name, safe='')}"
    response = requests.put(
        url,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"system_prompt": AUTOPARTS_BENCHMARK_SYSTEM_PROMPT},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("system_prompt") != AUTOPARTS_BENCHMARK_SYSTEM_PROMPT:
        raise RuntimeError("Agent API did not persist the expected benchmark policy")
    return payload
